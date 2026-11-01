"""Measure NBBO spreads on in-play (gap) days and fit a gapper cost model (H2, 2026-10-18).

Stocks gapping on news trade with wider spreads than an average stock at the same price,
so the general calibration would flatter a gapper strategy. Samples a seeded random subset
of gap days (scripts/scan_gappers.py), persists the windows to ``spread_samples``, and
prints per-price-bucket half-spreads for the regular session. The fitted bucket is the
5%-trimmed MEAN half-spread: the expected cost of a trade is a mean, and spreads are
right-skewed, so a median would understate it.

Usage:
    python scripts/measure_gapper_spreads.py --gappers data/universe/gappers.csv --n 300
"""
from __future__ import annotations

import argparse
import csv
import logging
import random
from collections import defaultdict
from datetime import date

import numpy as np

from sgt.db.session import get_session, init_db
from sgt.intraday.quotes import AlpacaQuotes, SpreadSampler, persist_samples
from sgt.intraday.sources import DataSourceError

log = logging.getLogger("measure_gapper_spreads")
_BUCKETS = (("hs_under_1", 0.0, 1.0), ("hs_1_to_5", 1.0, 5.0), ("hs_5_to_20", 5.0, 20.0),
            ("hs_over_20", 20.0, float("inf")))


def trimmed_mean(x: list[float], trim: float = 0.05) -> float:
    a = np.sort(np.asarray(x, dtype=float))
    k = int(len(a) * trim)
    return float(a[k:len(a) - k].mean()) if len(a) > 2 * k else float("nan")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gappers", default="data/universe/gappers.csv")
    ap.add_argument("--min-prev-volume", type=float, default=50_000)
    ap.add_argument("--n", type=int, default=300, help="gap days to sample")
    ap.add_argument("--windows", type=int, default=8)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--pacing", type=float, default=0.3)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_db()

    with open(args.gappers, newline="") as fh:
        days = [(r["symbol"].upper(), date.fromisoformat(r["session"]))
                for r in csv.DictReader(fh) if float(r["prev_volume"]) >= args.min_prev_volume]
    random.Random(args.seed).shuffle(days)
    days = days[:args.n]

    client = AlpacaQuotes(feed="sip")
    sampler = SpreadSampler(client.fetch_quotes, pacing_s=args.pacing)
    by_bucket: dict[str, list[float]] = defaultdict(list)
    by_minute: dict[str, list[float]] = defaultdict(list)
    try:
        for i, (sym, d) in enumerate(days, 1):
            try:
                samples = sampler.sample_session(sym, d, windows=args.windows)
            except DataSourceError as exc:
                log.warning("sampling failed %s %s: %s", sym, d, exc)
                continue
            if samples:
                with get_session() as session:
                    persist_samples(session, samples)
            for s in samples:
                if s.segment != "rth" or not (s.rel_spread_median > 0):
                    continue
                hs = 0.5 * s.rel_spread_median
                for name, lo, hi in _BUCKETS:
                    if lo <= s.mid_price < hi:
                        by_bucket[name].append(hs)
                by_minute["open (9:30-10:30)" if s.minute_of_day < 630 else "after 10:30"].append(hs)
            if i % 25 == 0:
                log.info("%d/%d gap days sampled", i, len(days))
    finally:
        client.close()

    print("Gap-day RTH HALF-spread by price bucket (5%-trimmed mean | median | n windows)")
    for name, _lo, _hi in _BUCKETS:
        v = by_bucket.get(name, [])
        if v:
            print(f"  {name:<11} mean={100 * trimmed_mean(v):.3f}%  median={100 * float(np.median(v)):.3f}%  n={len(v)}")
    for k, v in by_minute.items():
        print(f"  {k:<18} mean={100 * trimmed_mean(v):.3f}%  n={len(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
