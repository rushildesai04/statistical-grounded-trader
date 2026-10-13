"""Walk-forward runner: no leakage into the lockbox or the future, idempotent trials,
deterministic across worker counts, DSR trial count == configs evaluated."""
from __future__ import annotations

import pytest

from sgt.db.session import get_session
from sgt.search import registry, runner
from sgt.search.runner import build_fold_sessions, run_search
from sgt.search.space import SearchSpace


def _session_set(bars_by_symbol):
    return frozenset(b.session_date for bars in bars_by_symbol.values() for b in bars)


def test_walk_forward_never_reads_lockbox_or_future(base_yaml, make_bars, monkeypatch):
    bars, days = make_bars(symbol="AAA", n_sessions=6, seed=1)
    bars_by_symbol = {"AAA": bars}
    lockbox_start = days[4]
    research_sessions = sorted(d for d in days if d < lockbox_start)
    expected = {
        frozenset(w) | frozenset(t)
        for w, t in build_fold_sessions(research_sessions, 2, 1)
    }

    seen: list[frozenset] = []
    real = runner.run_backtest

    def spy(config, fold_bars, **kw):
        seen.append(_session_set(fold_bars))
        return real(config, fold_bars, **kw)

    monkeypatch.setattr(runner, "run_backtest", spy)

    space = SearchSpace(base=base_yaml, axes={"trigger.fast": [9]})
    run_search(space, bars_by_symbol, folds=2, lockbox_start=lockbox_start,
               search_run_id="leak", persist=False, allow_unknown_float=True)

    assert seen, "run_backtest was never called"
    lockbox_days = {days[4], days[5]}
    for s in seen:
        assert not (s & lockbox_days), f"lockbox session leaked into a backtest: {s}"
        assert s in expected, f"backtest saw an unexpected (future-leaking) session set: {s}"


def test_in_fold_filter_excludes_warmup_session_trades(make_config, make_bars):
    cfg = make_config()
    bars, days = make_bars(symbol="AAA", n_sessions=4, seed=2)
    bars_by_symbol = {"AAA": bars}
    warmup, test = (days[0], days[1]), (days[2], days[3])

    oos = runner.evaluate_oos(
        cfg, bars_by_symbol, [(warmup, test)], starting_cash=100_000.0,
        run_tag="t", collect_trades=True, allow_unknown_float=True)
    assert not oos.trades.empty, "expected OOS trades"
    assert set(oos.trades["session_date"]) <= set(test)
    assert all(d not in set(oos.trades["session_date"]) for d in warmup)


def test_trials_recorded_once_per_config_per_run(base_yaml, make_bars):
    bars, days = make_bars(symbol="AAA", n_sessions=6, seed=1)
    space = SearchSpace(base=base_yaml, axes={"trigger.fast": [5, 9], "trigger.slow": [18, 24]})

    r1 = run_search(space, {"AAA": bars}, folds=2, lockbox_start=days[4],
                    search_run_id="r1", persist=True, allow_unknown_float=True)
    with get_session() as s:
        first = registry.trial_count(s, "r1")
    assert first == r1.n_trials == 4

    # Re-running the same search_run_id must not double-count trials.
    run_search(space, {"AAA": bars}, folds=2, lockbox_start=days[4],
               search_run_id="r1", persist=True, allow_unknown_float=True)
    with get_session() as s:
        second = registry.trial_count(s, "r1")
    assert second == 4


def test_dsr_trial_count_equals_configs_evaluated(base_yaml, make_bars):
    bars, days = make_bars(symbol="AAA", n_sessions=6, seed=1)
    space = SearchSpace(base=base_yaml, axes={"trigger.fast": [5, 9, 12], "trigger.slow": [21, 24]})
    result = run_search(space, {"AAA": bars}, folds=2, lockbox_start=days[4],
                        search_run_id="dsr", persist=True, allow_unknown_float=True)
    assert result.n_trials == len(result.trials) == 6  # all fast<slow
    with get_session() as s:
        assert registry.trial_count(s, "dsr") == 6


def test_deterministic_across_worker_counts(base_yaml, make_bars):
    bars, days = make_bars(symbol="AAA", n_sessions=6, seed=1)
    space = SearchSpace(base=base_yaml, axes={"trigger.fast": [5, 9], "trigger.slow": [18, 24]})
    kw = dict(folds=2, lockbox_start=days[4], persist=False)

    one = run_search(space, {"AAA": bars}, workers=1, search_run_id="w1", **kw, allow_unknown_float=True)
    two = run_search(space, {"AAA": bars}, workers=2, search_run_id="w2", **kw, allow_unknown_float=True)

    assert [t.config_hash for t in one.trials] == [t.config_hash for t in two.trials]
    for a, b in zip(one.trials, two.trials):
        assert a.returns == pytest.approx(b.returns, abs=1e-12)
        assert a.metrics["n_trades"] == b.metrics["n_trades"]
        assert a.metrics["expectancy"] == pytest.approx(b.metrics["expectancy"], abs=1e-12)
