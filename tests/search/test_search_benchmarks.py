"""Benchmarks expose exactly take_every / trader_v0 / random and run the promotion gate."""
from __future__ import annotations

from sgt.search.benchmarks import benchmark_candidate
from sgt.search.runner import build_fold_sessions


def test_benchmark_keys_exact_and_gate_runs(make_config, make_bars, baseline_yaml):
    cfg = make_config(fast=9, slow=24)
    bars, days = make_bars(symbol="AAA", n_sessions=6, seed=1)
    fold_sessions = build_fold_sessions(days[:5], folds=2, embargo=1)

    res = benchmark_candidate(
        cfg, {"AAA": bars}, fold_sessions, starting_cash=100_000.0, seed=0,
        trader_v0_path=baseline_yaml, allow_unknown_float=True)

    assert set(res.baselines) == {"take_every", "trader_v0", "random"}
    opponents = {c.opponent for c in res.decision.comparisons}
    assert {"take_every", "trader_v0", "random"} <= opponents
    for name, metrics in res.baselines.items():
        assert len(metrics["returns"]) >= 2, f"{name} produced too few trades to compare"
    assert 0.0 < res.random_rate <= 1.0
    assert isinstance(res.decision.promote, bool)
