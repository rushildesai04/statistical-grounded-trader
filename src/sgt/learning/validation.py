"""Validation statistics for honest parameter search (plan §1, §10).

Everything here answers one question: is an observed edge real, or an artifact of
trying many configurations? The centerpiece is the Deflated Sharpe Ratio (Bailey &
López de Prado, 2014), which discounts an observed Sharpe by the Sharpe you would
expect from the best of ``n_trials`` random configurations.

Conventions
- Sharpe ratios here are PER-OBSERVATION (per trade), not annualized. Annualize at
  the reporting layer; the statistics below are scale-consistent in per-trade units.
- Kurtosis is the non-excess fourth standardized moment (normal == 3), matching the
  Bailey-LdP formulas.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import comb
from typing import Callable, Iterator, Sequence

import numpy as np
from scipy import stats

_EULER_MASCHERONI = 0.5772156649015329


# ------------------------------------------------------------------ Sharpe stats


def _sharpe_moments(sr: float, skew: float, kurtosis: float) -> float:
    """Variance-of-SR denominator term from Bailey-LdP PSR (kurtosis non-excess)."""
    return 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr ** 2


def probabilistic_sharpe_ratio(
    observed_sr: float, benchmark_sr: float, n_obs: int, skew: float, kurtosis: float
) -> float:
    """P(true SR > benchmark_sr) given the estimate's non-normal sampling error.

    ``observed_sr``/``benchmark_sr`` are per-observation Sharpe ratios.
    """
    if n_obs < 2:
        raise ValueError("probabilistic_sharpe_ratio requires n_obs >= 2")
    denom_var = _sharpe_moments(observed_sr, skew, kurtosis)
    if denom_var <= 0:
        # Degenerate moment structure; treat as undetermined -> no confidence.
        return 0.0
    z = (observed_sr - benchmark_sr) * np.sqrt(n_obs - 1) / np.sqrt(denom_var)
    return float(stats.norm.cdf(z))


def expected_max_sharpe(n_trials: int, sr_variance_across_trials: float) -> float:
    """Expected maximum Sharpe of ``n_trials`` independent null strategies.

    Bailey-LdP Euler-Mascheroni approximation. Returns 0 when there is a single
    trial or no cross-trial variance (nothing to deflate for).
    """
    if n_trials <= 1 or sr_variance_across_trials <= 0:
        return 0.0
    sigma = np.sqrt(sr_variance_across_trials)
    n = float(n_trials)
    z1 = stats.norm.ppf(1.0 - 1.0 / n)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n * np.e))
    return float(sigma * ((1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2))


def deflated_sharpe_ratio(
    observed_sr: float,
    n_trials: int,
    sr_variance_across_trials: float,
    n_obs: int,
    skew: float,
    kurtosis: float,
) -> float:
    """Probability the true Sharpe exceeds the expected max under the null.

    Equals ``probabilistic_sharpe_ratio`` against a zero benchmark when
    ``n_trials == 1`` (or cross-trial variance is 0), and decreases monotonically
    as ``n_trials`` grows (the deflation benchmark rises).
    """
    sr0 = expected_max_sharpe(n_trials, sr_variance_across_trials)
    return probabilistic_sharpe_ratio(observed_sr, sr0, n_obs, skew, kurtosis)


# ------------------------------------------------------------------ bootstrap / basics


def bootstrap_ci(
    values: Sequence[float],
    stat: Callable[[np.ndarray], float] = np.mean,
    n: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap CI of ``stat`` over ``values`` (deterministic via seed)."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        raise ValueError("bootstrap_ci requires at least one value")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n, arr.size))
    resamples = arr[idx]
    if stat is np.mean:
        estimates = resamples.mean(axis=1)
    else:
        estimates = np.array([stat(resamples[i]) for i in range(n)], dtype=float)
    lo = float(np.quantile(estimates, alpha / 2.0))
    hi = float(np.quantile(estimates, 1.0 - alpha / 2.0))
    return lo, hi


def profit_factor(returns: Sequence[float]) -> float:
    r = np.asarray(returns, dtype=float)
    gains = r[r > 0].sum()
    losses = -r[r < 0].sum()
    if losses == 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def expectancy(returns: Sequence[float]) -> float:
    r = np.asarray(returns, dtype=float)
    return float(r.mean()) if r.size else 0.0


def max_drawdown(returns: Sequence[float]) -> float:
    """Max peak-to-trough of the cumulative (additive) PnL. Non-negative."""
    r = np.asarray(returns, dtype=float)
    if r.size == 0:
        return 0.0
    equity = np.cumsum(r)
    peak = np.maximum.accumulate(equity)
    return float((peak - equity).max())


# ------------------------------------------------------------------ CV splitters


def purged_walk_forward_splits(
    timestamps: Sequence, n_splits: int, embargo: int = 0
) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    """Expanding-window walk-forward with purge+embargo (plan §10).

    Yields ``(train_idx, test_idx)`` as positional indices into ``timestamps``
    sorted ascending. Train is strictly before each contiguous test block, with the
    final ``embargo`` samples immediately preceding the test block purged so a label
    computed over them cannot overlap the test window. Forward-only => no future
    leakage into train.
    """
    if n_splits < 1:
        raise ValueError("n_splits must be >= 1")
    if embargo < 0:
        raise ValueError("embargo must be >= 0")
    order = np.argsort(np.asarray(timestamps), kind="stable")
    n = len(order)
    if n == 0:
        return
    blocks = np.array_split(np.arange(n), n_splits + 1)
    for k in range(1, len(blocks)):
        test_block = blocks[k]
        if test_block.size == 0:
            continue
        test_start = test_block[0]
        train_end = test_start - embargo
        if train_end <= 0:
            continue
        train_pos = np.arange(train_end)
        yield order[train_pos], order[test_block]


def cpcv_splits(n_groups: int, k_test: int) -> Iterator[tuple[tuple[int, ...], tuple[int, ...]]]:
    """Combinatorially purged CV group assignments (plan §10).

    Yields every way to choose ``k_test`` of ``n_groups`` contiguous groups as the
    test set — exactly ``C(n_groups, k_test)`` splits. Train groups exclude the test
    groups AND any group adjacent to a test group (purged to prevent label overlap
    across the train/test boundary).
    """
    if not 1 <= k_test < n_groups:
        raise ValueError("require 1 <= k_test < n_groups")
    all_groups = range(n_groups)
    for test in combinations(all_groups, k_test):
        test_set = set(test)
        adjacent = {g + d for g in test_set for d in (-1, 1)}
        train = tuple(g for g in all_groups if g not in test_set and g not in adjacent)
        yield train, tuple(test)


def cpcv_split_count(n_groups: int, k_test: int) -> int:
    """Number of CPCV splits == C(n_groups, k_test)."""
    return comb(n_groups, k_test)


# ------------------------------------------------------------------ acceptance


@dataclass(frozen=True, slots=True)
class Criterion:
    name: str
    passed: bool
    value: float
    threshold: float
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AcceptanceReport:
    accepted: bool
    criteria: tuple[Criterion, ...]
    expectancy: float
    expectancy_ci: tuple[float, float]
    deflated_sharpe: float
    profit_factor: float
    max_drawdown: float
    n_trades: int

    def as_dict(self) -> dict:
        return {
            "accepted": self.accepted,
            "expectancy": self.expectancy,
            "expectancy_ci": list(self.expectancy_ci),
            "deflated_sharpe": self.deflated_sharpe,
            "profit_factor": self.profit_factor,
            "max_drawdown": self.max_drawdown,
            "n_trades": self.n_trades,
            "criteria": {c.name: {"pass": c.passed, "value": c.value, "threshold": c.threshold}
                         for c in self.criteria},
        }


def evaluate_acceptance(
    trade_returns: Sequence[float],
    n_trials: int,
    *,
    sr_variance_across_trials: float | None = None,
    dsr_threshold: float = 0.95,
    pf_threshold: float = 1.3,
    max_dd_budget: float | None = None,
    alpha: float = 0.05,
    seed: int = 0,
) -> AcceptanceReport:
    """Apply the plan §1 go-live thresholds to a per-trade net-return series.

    Criteria: expectancy 95% bootstrap CI lower bound > 0; Deflated Sharpe >
    ``dsr_threshold``; profit factor >= ``pf_threshold``; and (when
    ``max_dd_budget`` is given) max drawdown <= budget.

    ``sr_variance_across_trials`` is the variance of the Sharpe estimates across all
    searched configs; when unknown it defaults to the null sampling variance of the
    Sharpe estimator so DSR still deflates for ``n_trials``.
    """
    r = np.asarray(trade_returns, dtype=float)
    if r.size < 2:
        raise ValueError("evaluate_acceptance requires >= 2 trades")

    exp = expectancy(r)
    ci_lo, ci_hi = bootstrap_ci(r, np.mean, alpha=alpha, seed=seed)

    std = float(r.std(ddof=1))
    sr = exp / std if std > 0 else 0.0
    skew = float(stats.skew(r)) if r.size >= 3 else 0.0
    kurt = float(stats.kurtosis(r, fisher=False)) if r.size >= 4 else 3.0
    if sr_variance_across_trials is None:
        sr_variance_across_trials = _sharpe_moments(sr, skew, kurt) / (r.size - 1)
    dsr = deflated_sharpe_ratio(sr, n_trials, sr_variance_across_trials, r.size, skew, kurt)

    pf = profit_factor(r)
    dd = max_drawdown(r)

    criteria = [
        Criterion("expectancy_ci_lower_positive", ci_lo > 0, ci_lo, 0.0,
                  f"95% CI [{ci_lo:.5f}, {ci_hi:.5f}]"),
        Criterion("deflated_sharpe", dsr > dsr_threshold, dsr, dsr_threshold),
        Criterion("profit_factor", pf >= pf_threshold, pf, pf_threshold),
    ]
    if max_dd_budget is not None:
        criteria.append(Criterion("max_drawdown_within_budget", dd <= max_dd_budget, dd, max_dd_budget))

    return AcceptanceReport(
        accepted=all(c.passed for c in criteria),
        criteria=tuple(criteria),
        expectancy=exp,
        expectancy_ci=(ci_lo, ci_hi),
        deflated_sharpe=dsr,
        profit_factor=pf,
        max_drawdown=dd,
        n_trades=int(r.size),
    )
