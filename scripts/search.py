"""Run a bounded parameter/combination search and write candidate cards (plan §10).

Loads a declarative search space, evaluates every grammar-valid config by session-level
walk-forward (lockbox sessions untouched), selects the broad-plateau centre rather than
the single best cell, benchmarks it against take-every / trader_v0 / random, opens the
lockbox once for the finalist, and writes the candidate card.

Usage::

    python scripts/search.py --space search/ema_grid.yaml --start 2025-06-01 \
        --end 2025-10-01 --lockbox-start 2025-09-01 --folds 4 --out out/card.md
"""
from __future__ import annotations

import argparse
from datetime import date

from sgt.catalysts.reference import ReferenceDataProvider
from sgt.db.session import init_db
from sgt.intraday.store import ParquetBarStore
from sgt.search.benchmarks import benchmark_candidate
from sgt.search.lockbox import LockboxAlreadyOpened, open_lockbox
from sgt.search.report import render_candidate_card
from sgt.search.runner import run_search
from sgt.search.select import plateau_select
from sgt.search.space import SearchSpace


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Bounded walk-forward strategy search.")
    ap.add_argument("--space", required=True, help="search space YAML")
    ap.add_argument("--start", required=True, help="first session date YYYY-MM-DD")
    ap.add_argument("--end", required=True, help="last session date YYYY-MM-DD")
    ap.add_argument("--lockbox-start", required=True, help="first held-out session YYYY-MM-DD")
    ap.add_argument("--folds", type=int, default=4, help="walk-forward folds (default 4)")
    ap.add_argument("--data", default="data", help="Parquet store root (default: data)")
    ap.add_argument("--cash", type=float, default=100_000.0, help="starting cash")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--metric", default="expectancy", help="selection metric (default expectancy)")
    ap.add_argument("--run-id", default="search", help="search_run_id for the trials registry")
    ap.add_argument("--out", default=None, help="candidate-card markdown output path")
    ap.add_argument("--allow-unknown-float", action="store_true",
                    help="admit symbols with no free-float data (research only; the universe "
                         "float cap then cannot be enforced). Default: fail closed.")
    args = ap.parse_args(argv)

    init_db()
    space = SearchSpace.from_yaml(args.space)
    symbols = [s.upper() for s in space.symbols]
    if not symbols:
        raise SystemExit("search space must list 'symbols'")
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    lockbox_start = date.fromisoformat(args.lockbox_start)

    # Point-in-time float/dilution from collected EDGAR data (scripts: catalysts collectors).
    uni = {"reference": ReferenceDataProvider(), "allow_unknown_float": args.allow_unknown_float}
    store = ParquetBarStore(args.data)
    bars_by_symbol = {}
    for sym in symbols:
        bars = store.read(sym, start, end)
        if bars:
            bars_by_symbol[sym] = bars
    if not bars_by_symbol:
        raise SystemExit(f"no bars for {symbols} in {start}..{end} under {args.data}")

    result = run_search(
        space, bars_by_symbol, folds=args.folds, lockbox_start=lockbox_start,
        workers=args.workers, seed=args.seed, search_run_id=args.run_id,
        starting_cash=args.cash, **uni)
    print(f"evaluated {result.n_trials} configs; skips={result.skips}")

    choice = plateau_select(result, metric=args.metric, min_trades=space.min_trades)
    if choice is None:
        raise SystemExit("no eligible plateau (no config met the min-trades floor)")
    trial = choice.trial
    print(f"plateau centre: {trial.config_hash} center={choice.center} "
          f"score={choice.score:.5f} {args.metric}={choice.cell_metric:.5f}")

    bench = benchmark_candidate(
        trial.config, bars_by_symbol, result.fold_sessions,
        starting_cash=args.cash, seed=args.seed, **uni)

    lockbox_report = None
    try:
        lockbox_report = open_lockbox(
            trial.config, bars_by_symbol, lockbox_start,
            search_run_id=args.run_id, starting_cash=args.cash, seed=args.seed, **uni)
    except LockboxAlreadyOpened as e:
        print(f"lockbox: {e}")
    except ValueError as e:
        print(f"lockbox skipped: {e}")

    card = render_candidate_card(
        result, trial, bars_by_symbol, benchmark=bench, lockbox=lockbox_report,
        engine_reference=uni["reference"], allow_unknown_float=args.allow_unknown_float)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(card)
        print(f"wrote {args.out}")
    else:
        print(card)


if __name__ == "__main__":
    main()
