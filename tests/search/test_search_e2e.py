"""End-to-end tiny search over synthetic multi-session data completes quickly (plan §10)."""
from __future__ import annotations

import time

from sgt.search.benchmarks import benchmark_candidate
from sgt.search.lockbox import open_lockbox
from sgt.search.report import render_candidate_card
from sgt.search.runner import run_search
from sgt.search.select import plateau_select
from sgt.search.space import SearchSpace


def test_end_to_end_under_60s(base_yaml, baseline_yaml, make_bars):
    t0 = time.monotonic()
    bars, days = make_bars(symbol="AAA", n_sessions=8, seed=3)
    bars_by_symbol = {"AAA": bars}
    lockbox_start = days[6]

    space = SearchSpace(base=base_yaml, axes={"trigger.fast": [5, 9], "trigger.slow": [18, 24]})
    result = run_search(space, bars_by_symbol, folds=3, lockbox_start=lockbox_start,
                        search_run_id="e2e", persist=True, allow_unknown_float=True)
    assert result.n_trials == 4

    choice = plateau_select(result, metric="expectancy", min_trades=1)
    assert choice is not None
    trial = choice.trial

    bench = benchmark_candidate(
        trial.config, bars_by_symbol, result.fold_sessions, starting_cash=100_000.0,
        seed=0, trader_v0_path=baseline_yaml, allow_unknown_float=True)

    lockbox_report = open_lockbox(
        trial.config, bars_by_symbol, lockbox_start, search_run_id="e2e", allow_unknown_float=True)

    card = render_candidate_card(
        result, trial, bars_by_symbol, benchmark=bench, lockbox=lockbox_report, allow_unknown_float=True)

    assert "# Candidate" in card
    assert "Out-of-sample metrics" in card
    assert "Benchmarks & promotion" in card
    assert "Lockbox" in card
    assert time.monotonic() - t0 < 60.0
