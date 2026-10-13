"""Audit a bar source's coverage and write a finding (docs/IMPLEMENTATION_PLAN.md §4.2).

Usage::

    python scripts/data_audit.py --source alpaca --symbols WFF,YMAT --start 2025-09-01 --end 2025-09-30
    python scripts/data_audit.py --source yfinance --symbols WFF --start 2025-10-06 --end 2025-10-10 \
        --out docs/findings/data_audit_2025-10-10.md
"""
from __future__ import annotations

import argparse
from datetime import date

from sgt.intraday.audit import audit_source
from sgt.intraday.sources import AlpacaHistoricalBars, YFinanceBars
from sgt.util import utcnow


def _make_source(name: str):
    if name == "yfinance":
        return YFinanceBars()
    if name == "alpaca":
        return AlpacaHistoricalBars()
    raise SystemExit(f"unknown source {name!r} (expected yfinance|alpaca)")


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit a 1-minute bar source.")
    ap.add_argument("--source", required=True, choices=["yfinance", "alpaca"])
    ap.add_argument("--symbols", required=True, help="comma-separated tickers")
    ap.add_argument("--start", required=True, help="start date YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="end date YYYY-MM-DD")
    ap.add_argument("--out", default=None, help="markdown output path")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    source = _make_source(args.source)

    report = audit_source(source, symbols, start, end)
    md = report.to_markdown()

    out = args.out or f"docs/findings/data_audit_{utcnow().date().isoformat()}.md"
    from pathlib import Path

    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(md)
    print(md)
    print(f"written: {out}")


if __name__ == "__main__":
    main()
