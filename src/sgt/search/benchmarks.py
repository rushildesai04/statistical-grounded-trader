"""Benchmark a selected candidate against reference policies (plan §8, §9.2).

Every candidate is scored on the SAME out-of-sample folds against three references:

- ``take_every`` -- the candidate's trigger with setup/confirm/filter/meta bypassed:
  isolates the value of the selection rules over "take every crossover".
- ``trader_v0``  -- the hand-written rules-only baseline strategy.
- ``random``     -- seeded random entries at the candidate's own entry frequency with
  the candidate's exits: separates entry skill from exit / regime luck.

The three metric dicts (keyed exactly ``take_every``, ``trader_v0``, ``random``) are
passed to :func:`sgt.learning.promotion.promotion_gate`, which requires the candidate
to beat every one with a bootstrap-CI margin strictly above zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

from sgt.core.types import Bar
from sgt.learning.promotion import PromotionDecision, promotion_gate
from sgt.search.runner import FoldSessions, evaluate_oos
from sgt.strategy.loader import load_strategy
from sgt.strategy.runtime import EntryOverride
from sgt.strategy.schema import StrategyConfig

DEFAULT_V0_PATH = "strategies/trader_v0.yaml"
_MIN_RATE = 1.0e-4


@dataclass(slots=True)
class BenchmarkResult:
    candidate_returns: list[float]
    baselines: dict[str, dict]          # name -> {"returns": [...], "n_trades": int}
    decision: PromotionDecision
    random_rate: float


def benchmark_candidate(
    candidate: StrategyConfig,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    fold_sessions: FoldSessions,
    *,
    starting_cash: float,
    seed: int = 0,
    trader_v0_path: str | Path = DEFAULT_V0_PATH,
    champion_metrics: Mapping | None = None,
    reference: object = None,
    allow_unknown_float: bool = False,
) -> BenchmarkResult:
    """Score ``candidate`` and its three references on ``fold_sessions`` and run the gate.

    All four runs share the same universe data (``reference`` / ``allow_unknown_float``)
    so benchmarks differ only in entry selection."""
    uni = {"reference": reference, "allow_unknown_float": allow_unknown_float}
    cand = evaluate_oos(candidate, bars_by_symbol, fold_sessions,
                        starting_cash=starting_cash, run_tag="bench:candidate", **uni)
    take_every = evaluate_oos(
        candidate, bars_by_symbol, fold_sessions, starting_cash=starting_cash,
        run_tag="bench:take_every", entry_override=EntryOverride(mode="take_every"), **uni)
    v0_config = load_strategy(trader_v0_path)
    v0 = evaluate_oos(v0_config, bars_by_symbol, fold_sessions,
                      starting_cash=starting_cash, run_tag="bench:trader_v0", **uni)

    # Random enters on a trigger with probability = candidate entries / trigger fires,
    # so it takes the same number of entries, chosen at random among the triggers.
    rate = (cand.n_entries / take_every.n_entries) if take_every.n_entries > 0 else _MIN_RATE
    rate = min(1.0, max(_MIN_RATE, rate))
    rnd = evaluate_oos(
        candidate, bars_by_symbol, fold_sessions, starting_cash=starting_cash,
        run_tag="bench:random",
        entry_override=EntryOverride(mode="random", rate=rate, seed=seed), **uni)

    baselines = {
        "take_every": {"returns": list(take_every.returns), "n_trades": take_every.n_entries},
        "trader_v0": {"returns": list(v0.returns), "n_trades": v0.n_entries},
        "random": {"returns": list(rnd.returns), "n_trades": rnd.n_entries},
    }
    candidate_metrics = {"returns": list(cand.returns)}
    decision = promotion_gate(
        candidate_metrics, baselines, champion_metrics, seed=seed)
    return BenchmarkResult(
        candidate_returns=list(cand.returns), baselines=baselines,
        decision=decision, random_rate=float(rate))
