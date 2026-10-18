"""Backfill 1-minute SIP bars for the gap-day universe (H2, 2026-10-18).

For every gap day in the scan CSV (scripts/scan_gappers.py) whose previous-day volume
clears --min-prev-volume (known before the open), fetch the gap session plus the
--lookback prior sessions the relative-volume features need. One contiguous window per
symbol; sessions already in the store are skipped (immutable store).

Usage:
    python scripts/backfill_gappers.py --gappers data/universe/gappers.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from backfill_alpaca import backfill
from sgt.intraday.calendar import default_calendar
from sgt.intraday.sources import AlpacaHistoricalBars
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger("backfill_gappers")


def gap_days(path: Path, min_prev_volume: float) -> dict[str, list[date]]:
    out: dict[str, list[date]] = defaultdict(list)
    with path.open() as fh:
        for row in csv.DictReader(fh):
            if float(row["prev_volume"]) >= min_prev_volume:
                out[row["symbol"].upper()].append(date.fromisoformat(row["session"]))
    return out


def window_start(first: date, lookback: int) -> date:
    cal, d, n = default_calendar(), first, 0
    while n < lookback:
        d -= timedelta(days=1)
        if cal.is_session(d):
            n += 1
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gappers", type=Path, default=Path("data/universe/gappers.csv"))
    ap.add_argument("--min-prev-volume", type=float, default=50_000)
    ap.add_argument("--lookback", type=int, default=21)
    ap.add_argument("--data", default="data/alpaca")
    ap.add_argument("--pacing", type=float, default=0.3)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    days = gap_days(args.gappers, args.min_prev_volume)
    store = ParquetBarStore(args.data)
    src = AlpacaHistoricalBars(feed="sip")
    totals: dict[str, int] = defaultdict(int)
    try:
        for i, (sym, ds) in enumerate(sorted(days.items()), 1):
            st = backfill([sym], window_start(min(ds), args.lookback), max(ds), store=store,
                          fetch=src.fetch, pacing_s=args.pacing)
            for k, v in st.items():
                totals[k] += v
            if i % 50 == 0 or i == len(days):
                log.info("%d/%d symbols; %s", i, len(days), dict(totals))
    finally:
        src.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
