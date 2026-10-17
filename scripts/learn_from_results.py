"""Weekly "learn from results" loop (plan §9, §10, §11.2).

Runs the full diagnostics pipeline over stored 1-minute bars and writes a markdown
report that PROPOSES changes for a human to approve. It never changes live trading.

Pipeline: cost attribution (gross vs net, config vs take-every) -> realistic
forward outcome of every trigger -> net-by-context buckets -> stability across the
two halves of the data -> learned-filter walk-forward -> conservative, rule-based
recommendations.

Usage::

    python scripts/learn_from_results.py --strategy strategies/trader_v0.yaml \
        --start 2026-09-01 --end 2026-10-12 [--data data] [--allow-unknown-float] \
        [--out reports/learning_2026-10-17.md]
"""
from __future__ import annotations

import argparse
from datetime import date

from sgt.brokers.costs import CostModel
from sgt.catalysts.reference import ReferenceDataProvider
from sgt.db.session import init_db
from sgt.learning.diagnostics import (
    cost_attribution,
    learned_filter_walkforward,
    signal_outcomes,
    stable_buckets,
)
from sgt.learning.diagnostics_report import (
    DEFAULT_BUCKET_SPECS,
    recommendations,
    render_report,
)
from sgt.intraday.store import ParquetBarStore
from sgt.strategy.loader import load_strategy


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Weekly learning-from-results report.")
    ap.add_argument("--strategy", required=True, help="strategy YAML path")
    ap.add_argument("--start", required=True, help="start session date YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="end session date YYYY-MM-DD")
    ap.add_argument("--data", default="data", help="Parquet store root (default: data)")
    ap.add_argument("--allow-unknown-float", action="store_true",
                    help="admit symbols with no free-float data for the CONFIG variant "
                         "(research only; the universe float cap cannot be enforced).")
    ap.add_argument("--out", default=None,
                    help="markdown output path (default: reports/learning_<end>.md)")
    args = ap.parse_args(argv)

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if end < start:
        raise SystemExit(f"--end {end} is before --start {start}")

    config = load_strategy(args.strategy)
    store = ParquetBarStore(args.data)
    bars_by_symbol = {}
    for sym in store.symbols(None):
        bars = store.read(sym, start, end)
        if bars:
            bars_by_symbol[sym] = bars
    if not bars_by_symbol:
        raise SystemExit(f"no bars found in {start}..{end} under {args.data}")

    init_db()
    cost_model = CostModel()
    reference = ReferenceDataProvider()

    attr = cost_attribution(config, bars_by_symbol, reference=reference,
                            allow_unknown_float=args.allow_unknown_float,
                            cost_model=cost_model)

    def bar_loader(symbol: str):
        return bars_by_symbol.get(symbol.upper(), ())

    outcomes = signal_outcomes(attr.take_every_candidates, bar_loader, cost_model=cost_model)
    stable = stable_buckets(outcomes, DEFAULT_BUCKET_SPECS)
    wfo = learned_filter_walkforward(outcomes)
    recs = recommendations(outcomes, stable, wfo, cost_attr=attr)

    report = render_report(outcomes=outcomes, cost_model=cost_model, recommendations=recs,
                           cost_attr=attr, stable=stable, walkforward=wfo,
                           as_of=str(end))

    out_path = args.out or f"reports/learning_{end}.md"
    from pathlib import Path

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(report, encoding="utf-8")
    print(f"wrote {out_path} ({len(outcomes)} signals, "
          f"{int(stable['stable'].sum()) if not stable.empty else 0} stable buckets)")


if __name__ == "__main__":
    main()
