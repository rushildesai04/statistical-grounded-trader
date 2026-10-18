"""Measure real NBBO spreads from Alpaca SIP and compare them to the modeled costs.

For each (symbol, session) it samples short windows across the session's segments,
derives a time-weighted median relative spread per window, writes them to the
``spread_samples`` table, and prints the measured-vs-modeled half-spread table the
owner needs (per price bucket and per segment). See docs/DECISION_MODEL.md §10 (F1).

Usage:
    python scripts/measure_spreads.py --start 2026-10-03 --end 2026-10-16
    python scripts/measure_spreads.py --symbols WFF,YMAT --start 2026-10-03 --end 2026-10-16 --windows 8

Default symbols: every symbol present in the Alpaca bar store (data/alpaca). Windows
newer than 15 minutes are skipped (the free SIP plan rejects them with HTTP 403).
"""
from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from datetime import date
from statistics import median
from typing import Sequence

from sgt.brokers.costs import DEFAULT_COST_MODEL, CostModel
from sgt.db.session import get_session, init_db
from sgt.intraday.calendar import default_calendar
from sgt.intraday.quotes import AlpacaQuotes, SpreadSample, SpreadSampler, persist_samples
from sgt.intraday.sources import DataSourceError
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger("measure_spreads")

_PRICE_BUCKETS = (("< $1", 0.0, 1.0), ("$1-5", 1.0, 5.0), ("$5-20", 5.0, 20.0), ("> $20", 20.0, float("inf")))


def _price_bucket(price: float) -> str:
    for label, lo, hi in _PRICE_BUCKETS:
        if lo <= price < hi:
            return label
    return "> $20"


def run(
    symbols: Sequence[str],
    start: date,
    end: date,
    *,
    windows: int,
    pacing_s: float,
    sampler: SpreadSampler,
) -> list[SpreadSample]:
    cal = default_calendar()
    sessions = [d for d in _sessions_between(cal, start, end)]
    collected: list[SpreadSample] = []
    done = _already_sampled()
    skipped = 0
    for sym in symbols:
        for d in sessions:
            if (sym, d.isoformat()) in done:
                skipped += 1  # resumable: never re-download a sampled symbol-session
                continue
            try:
                samples = sampler.sample_session(sym, d, windows=windows)
            except DataSourceError as exc:
                log.warning("sampling failed %s %s: %s", sym, d, exc)
                continue
            if not samples:
                continue
            with get_session() as session:
                persist_samples(session, samples)
            collected.extend(samples)
            log.info("%s %s: %d windows sampled", sym, d, len(samples))
    log.info("skipped %d already-sampled symbol-sessions", skipped)
    return collected


def _already_sampled() -> set[tuple[str, str]]:
    from sgt.intraday.quotes import SpreadSampleRow

    with get_session() as session:
        rows = session.query(SpreadSampleRow.symbol, SpreadSampleRow.session_date).distinct().all()
    return {(sym, d if isinstance(d, str) else d.isoformat()) for sym, d in rows}


def _sessions_between(cal, start: date, end: date) -> list[date]:
    out: list[date] = []
    d = start
    from datetime import timedelta
    while d <= end:
        if cal.is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def comparison_table(samples: Sequence[SpreadSample], cost_model: CostModel) -> str:
    """Median measured vs modeled half-spread, grouped by price bucket and by segment."""
    by_price: dict[str, list[tuple[float, float]]] = defaultdict(list)
    by_seg: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for s in samples:
        measured_hs = 0.5 * s.rel_spread_median
        modeled_hs = cost_model.half_spread_pct(s.mid_price)
        by_price[_price_bucket(s.mid_price)].append((measured_hs, modeled_hs))
        by_seg[s.segment].append((measured_hs, modeled_hs))

    def _rows(groups: dict[str, list[tuple[float, float]]], order: Sequence[str]) -> list[str]:
        rows = []
        for key in order:
            vals = groups.get(key)
            if not vals:
                continue
            meas = median(m for m, _ in vals)
            mod = median(x for _, x in vals)
            ratio = (meas / mod) if mod > 0 else float("nan")
            rows.append(f"  {key:<8} n={len(vals):>5}  measured={meas * 100:6.3f}%  "
                        f"modeled={mod * 100:6.3f}%  meas/mod={ratio:4.2f}x")
        return rows

    lines = [f"Measured vs modeled HALF-spread (n windows={len(samples)})", "", "By price bucket:"]
    lines += _rows(by_price, [b[0] for b in _PRICE_BUCKETS])
    lines += ["", "By segment:"]
    lines += _rows(by_seg, ["pre", "rth", "post"])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Measure NBBO spreads from Alpaca SIP.")
    ap.add_argument("--symbols", default="", help="comma list (default: symbols in the Alpaca store)")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--windows", type=int, default=8, help="windows per session (default 8)")
    ap.add_argument("--data", default="data/alpaca", help="bar store root (default data/alpaca)")
    ap.add_argument("--pacing", type=float, default=0.35, help="seconds between requests")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()

    store = ParquetBarStore(args.data)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or store.symbols()
    if not symbols:
        raise SystemExit("no symbols (backfill data/alpaca first or pass --symbols)")

    client = AlpacaQuotes(feed="sip")
    sampler = SpreadSampler(client.fetch_quotes, pacing_s=args.pacing)
    started = time.time()
    try:
        samples = run(symbols, date.fromisoformat(args.start), date.fromisoformat(args.end),
                      windows=args.windows, pacing_s=args.pacing, sampler=sampler)
    finally:
        client.close()
    print(f"symbols={len(symbols)} windows_written={len(samples)} in {time.time() - started:.0f}s\n")
    print(comparison_table(samples, DEFAULT_COST_MODEL))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
