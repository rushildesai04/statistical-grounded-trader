"""Validation-statistics tests (plan §1, §10)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from sgt.learning.validation import (
    bootstrap_ci,
    cpcv_split_count,
    cpcv_splits,
    deflated_sharpe_ratio,
    evaluate_acceptance,
    expectancy,
    expected_max_sharpe,
    max_drawdown,
    probabilistic_sharpe_ratio,
    profit_factor,
    purged_walk_forward_splits,
)


def test_dsr_equals_psr_when_single_trial_zero_variance():
    sr, n, skew, kurt = 0.15, 250, 0.0, 3.0
    dsr = deflated_sharpe_ratio(sr, n_trials=1, sr_variance_across_trials=0.0,
                                n_obs=n, skew=skew, kurtosis=kurt)
    psr = probabilistic_sharpe_ratio(sr, 0.0, n, skew, kurt)
    assert dsr == pytest.approx(psr)


def test_dsr_decreases_with_more_trials():
    sr, n = 0.15, 250
    var = 0.01
    dsrs = [deflated_sharpe_ratio(sr, t, var, n, 0.0, 3.0) for t in (1, 10, 100, 1000)]
    assert all(a >= b for a, b in zip(dsrs, dsrs[1:]))
    assert dsrs[0] > dsrs[-1]


def test_expected_max_sharpe_grows_with_trials():
    v = 0.02
    assert expected_max_sharpe(1, v) == 0.0
    assert expected_max_sharpe(10, v) < expected_max_sharpe(1000, v)


def test_psr_monotone_in_observed_sr():
    lo = probabilistic_sharpe_ratio(0.05, 0.0, 300, 0.0, 3.0)
    hi = probabilistic_sharpe_ratio(0.25, 0.0, 300, 0.0, 3.0)
    assert hi > lo
    assert 0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0


def test_bootstrap_ci_brackets_mean_and_is_deterministic():
    rng = np.random.default_rng(0)
    vals = rng.normal(1.0, 0.5, size=500)
    lo1, hi1 = bootstrap_ci(vals, np.mean, seed=42)
    lo2, hi2 = bootstrap_ci(vals, np.mean, seed=42)
    assert (lo1, hi1) == (lo2, hi2)
    assert lo1 < vals.mean() < hi1


def test_profit_factor_expectancy_drawdown_known_values():
    r = [1.0, -0.5, 2.0, -1.0]
    assert profit_factor(r) == pytest.approx(3.0 / 1.5)
    assert expectancy(r) == pytest.approx(0.375)
    # equity path: 1, 0.5, 2.5, 1.5 -> peak 2.5, trough after 1.5 -> dd 1.0
    assert max_drawdown(r) == pytest.approx(1.0)
    assert math.isinf(profit_factor([1.0, 2.0]))


def test_purged_walk_forward_never_leaks():
    ts = np.arange(100)
    embargo = 3
    folds = list(purged_walk_forward_splits(ts, n_splits=4, embargo=embargo))
    assert len(folds) >= 1
    for train_idx, test_idx in folds:
        assert train_idx.size > 0 and test_idx.size > 0
        # forward-only with an embargo gap: last train ts precedes test start by >= embargo+1
        assert ts[train_idx].max() <= ts[test_idx].min() - embargo - 1


def test_cpcv_count_equals_n_choose_k():
    n_groups, k = 6, 2
    splits = list(cpcv_splits(n_groups, k))
    assert len(splits) == cpcv_split_count(n_groups, k) == math.comb(6, 2)
    for train, test in splits:
        assert len(test) == k
        # purged: no train group is adjacent to or inside the test set
        for g in test:
            assert g - 1 not in train and g + 1 not in train and g not in train


def test_acceptance_pass_case():
    rng = np.random.default_rng(1)
    r = rng.normal(0.02, 0.01, size=400)  # strong, stable positive edge
    report = evaluate_acceptance(r, n_trials=5, pf_threshold=1.3, max_dd_budget=1.0)
    assert report.accepted
    crit = {c.name: c.passed for c in report.criteria}
    assert crit["expectancy_ci_lower_positive"]
    assert crit["deflated_sharpe"]
    assert crit["profit_factor"]


def test_acceptance_fail_on_noise():
    rng = np.random.default_rng(2)
    r = rng.normal(0.0, 0.02, size=400)  # no edge
    report = evaluate_acceptance(r, n_trials=500, max_dd_budget=0.1)
    assert not report.accepted


def test_acceptance_fail_on_overtrialed_edge():
    rng = np.random.default_rng(3)
    r = rng.normal(0.01, 0.02, size=300)
    few = evaluate_acceptance(r, n_trials=1)
    many = evaluate_acceptance(r, n_trials=100_000)
    # Same returns, more trials -> DSR drops, can flip acceptance off.
    assert many.deflated_sharpe < few.deflated_sharpe
