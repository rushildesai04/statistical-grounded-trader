#!/usr/bin/env python
"""Build the decision-model dataset from the Parquet bar store.

One streaming pass per symbol (no backtest): triggers -> candidates -> point-in-time
features + round-trip cost + ATR-bracket labels (full grid) + uniqueness weights,
gated by discovery (``tracked_symbols.first_seen_session``; watchlist always).

Usage:
    python scripts/build_decision_dataset.py --store data/alpaca --out data/decision \
        [--dataset-id ID] [--watchlist AAA,BBB] [--symbols AAA,BBB]

Writes ``<out>/<dataset_id>/dataset.parquet`` and ``meta.json`` and prints a summary.
"""
from __future__ import annotations

import argparse
import logging
import sys

from sgt.decision.dataset import build_dataset
from sgt.decision.features import CostModelAdapter


def _symbols(arg: str | None) -> tuple[str, ...]:
    if not arg:
        return ()
    return tuple(s.strip().upper() for s in arg.split(",") if s.strip())


# Moved to sgt.brokers.costs (2026-10-18) so the trainer can reference it; re-exported.
from sgt.brokers.costs import CALIBRATED_COST_MODEL  # noqa: E402,F401


def _cost_source(kind: str):
    if kind == "model":
        return None  # builder default: bucket CostModel
    if kind == "calibrated":
        return CostModelAdapter(CALIBRATED_COST_MODEL, tag="calibrated")
    if kind == "gapper":
        from sgt.brokers.costs import (GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT,
                                       GAPPER_REST_MULT)
        return CostModelAdapter(GAPPER_COST_MODEL, tag="gapper",
                                open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                                rest_mult=GAPPER_REST_MULT)
    from sgt.brokers.measured_costs import MeasuredCostModel
    from sgt.db.session import get_session, init_db

    init_db()
    return MeasuredCostModel.from_db(get_session)


def _gap_days(path: str, min_prev_volume: float) -> dict[str, frozenset]:
    import csv
    from collections import defaultdict
    from datetime import date

    out: dict[str, set] = defaultdict(set)
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if float(row["prev_volume"]) >= min_prev_volume:
                out[row["symbol"].upper()].add(date.fromisoformat(row["session"]))
    return {k: frozenset(v) for k, v in out.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", default="data/alpaca", help="Parquet bar store root")
    parser.add_argument("--out", default="data/decision", help="output root")
    parser.add_argument("--dataset-id", default=None, help="dataset id (default: timestamp)")
    parser.add_argument("--watchlist", default=None, help="comma-separated always-active symbols")
    parser.add_argument("--symbols", default=None, help="restrict to these symbols (comma-separated)")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--research-ungated", action="store_true",
                        help="ignore discovery gating (momentum-biased; never promotion-eligible)")
    parser.add_argument("--cost", choices=("model", "calibrated", "gapper", "measured"), default="model",
                        help="cost source: bucket model, bucket model calibrated to measured "
                             "spreads (aggregate, no look-ahead), or per-symbol point-in-time "
                             "measured spreads (falls back to the model when unsampled)")
    parser.add_argument("--gap-days", default=None,
                        help="CSV from scripts/scan_gappers.py: admit candidates only on "
                             "these in-play days, from the open (H2 universe)")
    parser.add_argument("--min-prev-volume", type=float, default=50_000,
                        help="with --gap-days: minimum previous-day volume (known pre-open)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")

    summary = build_dataset(
        store_root=args.store,
        out_dir=args.out,
        dataset_id=args.dataset_id,
        watchlist=_symbols(args.watchlist),
        symbols=list(_symbols(args.symbols)) or None,
        research_ungated=args.research_ungated,
        cost_source=_cost_source(args.cost),
        active_days=(_gap_days(args.gap_days, args.min_prev_volume) if args.gap_days else None),
    )
    print(
        f"dataset_id={summary.dataset_id} rows={summary.rows} symbols={summary.symbols} "
        f"sessions={summary.sessions} truncated_labels={summary.truncated_labels} "
        f"elapsed_s={summary.elapsed_s:.1f}\npath={summary.out_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
