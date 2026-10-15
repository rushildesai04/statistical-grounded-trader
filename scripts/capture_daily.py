"""Daily data-accumulation job: discover movers, track them, capture their recent
1-minute sessions before free history expires, and snapshot float.

Run once per trading day (after the post-market close so the day's session is
complete). See docs/runbooks/intraday_operations.md §2 "Daily accumulation".

Usage::

    python scripts/capture_daily.py
    python scripts/capture_daily.py --watchlist WFF,YMAT,SPCX --keep-sessions 20
    python scripts/capture_daily.py --dry-run --report-out /tmp/plan.md
    python scripts/capture_daily.py --no-float --lookback-sessions 5 --max-symbols 200
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sgt.catalysts.filings import FilingsCollector
from sgt.db.session import init_db
from sgt.intraday.accumulate import default_float_source, default_screener, run_daily

_UTC = timezone.utc


def main() -> None:
    ap = argparse.ArgumentParser(description="Daily intraday data-accumulation job.")
    ap.add_argument("--watchlist", default="", help="comma-separated always-active tickers")
    ap.add_argument("--keep-sessions", type=int, default=20,
                    help="sessions a symbol stays tracked after its last discovery")
    ap.add_argument("--lookback-sessions", type=int, default=5,
                    help="recent sessions to capture per symbol (<= yfinance window)")
    ap.add_argument("--max-symbols", type=int, default=200,
                    help="cap on symbols captured per run (most-recently-seen first)")
    ap.add_argument("--count", type=int, default=100, help="quotes per screener query")
    ap.add_argument("--no-float", action="store_true",
                    help="skip both SEC and vendor float collection")
    ap.add_argument("--dry-run", action="store_true",
                    help="discover and plan only; write nothing")
    ap.add_argument("--report-out", default=None, help="write the markdown report here")
    args = ap.parse_args()

    init_db()
    watchlist = [s.strip().upper() for s in args.watchlist.split(",") if s.strip()]

    report = run_daily(
        datetime.now(_UTC),
        screener=default_screener,
        filings=None if args.no_float else FilingsCollector(),
        float_source=None if args.no_float else default_float_source,
        watchlist=watchlist,
        count=args.count,
        keep_sessions=args.keep_sessions,
        lookback_sessions=args.lookback_sessions,
        max_symbols=args.max_symbols,
        collect_sec_float=not args.no_float,
        collect_vendor_float=not args.no_float,
        dry_run=args.dry_run,
    )

    md = report.to_markdown()
    print(md)
    if args.report_out:
        with open(args.report_out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(f"report written to {args.report_out}")


if __name__ == "__main__":
    main()
