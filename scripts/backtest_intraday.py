"""Backtest one intraday strategy over stored 1-minute bars (plan §8, §11.2).

Loads bars from the immutable Parquet store, runs the single-engine backtester and
writes a markdown report (headline metrics, exit-reason attribution, skip reasons).

Usage::

    python scripts/backtest_intraday.py --strategy strategies/trader_v0.yaml \
        --symbols WFF,YMAT --start 2025-09-01 --end 2025-09-30 --out report.md
"""
from __future__ import annotations

import argparse
from datetime import date

from sgt.catalysts.reference import ReferenceDataProvider
from sgt.db.session import init_db
from sgt.engine.backtest import run_backtest
from sgt.engine.report import render_report
from sgt.intraday.store import ParquetBarStore
from sgt.strategy.loader import load_strategy


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Backtest an intraday strategy over stored bars.")
    ap.add_argument("--strategy", required=True, help="strategy YAML path")
    ap.add_argument("--symbols", required=True, help="comma-separated tickers")
    ap.add_argument("--start", required=True, help="start session date YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="end session date YYYY-MM-DD")
    ap.add_argument("--data", default="data", help="Parquet store root (default: data)")
    ap.add_argument("--cash", type=float, default=100_000.0, help="starting cash (default 100000)")
    ap.add_argument("--out", default=None, help="markdown output path (default: stdout)")
    ap.add_argument("--allow-unknown-float", action="store_true",
                    help="admit symbols with no free-float data (research only; the universe "
                         "float cap then cannot be enforced). Default: fail closed.")
    args = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if not symbols:
        raise SystemExit("no symbols given")
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    config = load_strategy(args.strategy)
    store = ParquetBarStore(args.data)
    bars_by_symbol = {}
    for sym in symbols:
        bars = store.read(sym, start, end)
        if bars:
            bars_by_symbol[sym] = bars
    if not bars_by_symbol:
        raise SystemExit(f"no bars found for {symbols} in {start}..{end} under {args.data}")

    init_db()
    result = run_backtest(config, bars_by_symbol, starting_cash=args.cash,
                          run_id=f"backtest:{config.id}", reference=ReferenceDataProvider(),
                          allow_unknown_float=args.allow_unknown_float)
    if args.allow_unknown_float:
        print("NOTE: --allow-unknown-float: universe float cap NOT enforced for symbols without float data")
    report = render_report(result)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"wrote {args.out} ({result.summary['n_trades']} trades)")
    else:
        print(report)


if __name__ == "__main__":
    main()
