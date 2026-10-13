"""Capture one session's 1-minute bars into the Parquet store.

Time-sensitive: free intraday history (yfinance ~7 days, Alpaca free SIP) expires,
so the trader's example day must be captured promptly.

Usage::

    python scripts/capture_session.py --symbols WFF,YMAT,SPCX --date 2026-10-12 --source yfinance
    python scripts/capture_session.py --symbols WFF --date 2026-10-12 --source alpaca --overwrite
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sgt.core.types import Bar
from sgt.intraday.sources import AlpacaHistoricalBars, YFinanceBars
from sgt.intraday.store import ParquetBarStore

_UTC = timezone.utc


def _make_source(name: str):
    if name == "yfinance":
        return YFinanceBars()
    if name == "alpaca":
        return AlpacaHistoricalBars()
    raise SystemExit(f"unknown source {name!r} (expected yfinance|alpaca)")


def capture(symbols: list[str], session_date: date, source_name: str, overwrite: bool) -> int:
    source = _make_source(source_name)
    start = datetime.combine(session_date, time.min, tzinfo=_UTC)
    end = datetime.combine(session_date + timedelta(days=1), time.min, tzinfo=_UTC)
    store = ParquetBarStore()
    written = 0
    for symbol in symbols:
        bars = source.fetch(symbol, start, end)
        by_session: dict[date, list[Bar]] = defaultdict(list)
        for b in bars:
            by_session[b.session_date].append(b)
        for sdate, sbars in by_session.items():
            sbars.sort(key=lambda b: b.ts)
            path = store.write_session(symbol, sdate, sbars, overwrite=overwrite)
            written += len(sbars)
            print(f"wrote {len(sbars):5d} bars  {symbol:<6} {sdate}  -> {path}")
        if not bars:
            print(f"no bars returned for {symbol} on {session_date} from {source_name}")
    return written


def main() -> None:
    ap = argparse.ArgumentParser(description="Capture a session's 1-minute bars to Parquet.")
    ap.add_argument("--symbols", required=True, help="comma-separated tickers")
    ap.add_argument("--date", required=True, help="session date YYYY-MM-DD")
    ap.add_argument("--source", required=True, choices=["yfinance", "alpaca"])
    ap.add_argument("--overwrite", action="store_true", help="replace existing session files")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    session_date = date.fromisoformat(args.date)
    total = capture(symbols, session_date, args.source, args.overwrite)
    print(f"done: {total} bars written")


if __name__ == "__main__":
    main()
