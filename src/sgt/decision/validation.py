"""Out-of-sample validation for the decision policy (DECISION_MODEL.md §8, §10).

Everything here answers: does the policy carry a real, cost-robust edge, or is it an
artifact of search? The pieces (all applied to the WHOLE policy, not just the model):

- ``session_cpcv``: combinatorially purged CV grouped by SESSION with an embargo of
  >= H (same-session labels mean a one-session embargo fully purges) so no fold trains
  on a test or embargoed session (F8).
- ``policy_trade_returns``: per-candidate policy P&L after costs using the sizing rule,
  with a cost multiplier for the +/-50% spread stress (F1).
- ``fold_diagnostics``: AUC, calibration (Brier), corr(mu, realized).
- ``cluster_bootstrap_ci``: CIs that resample (session, symbol) clusters, not rows (F4).
- per-fold expectancy sign agreement (F6), cost-stress sign-flip rejection (F1),
  label-shuffle null check (F8), and the Deflated Sharpe gate on the trial ledger (F5).
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Callable, Sequence

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from sgt.decision.policy import PolicyParams, lower_ci_probability
from sgt.learning.validation import deflated_sharpe_ratio

NULL_AUC_FLOOR = 0.03  # minimum half-width of the AUC null band, regardless of sample size
NULL_CORR_TOL = 0.15


# ------------------------------------------------------------------ CPCV splits


def session_cpcv(
    sessions: Sequence, n_groups: int, k_test: int, embargo: int = 1
) -> list[tuple[tuple, tuple]]:
    """Session-grouped combinatorially-purged CV (F8).

    ``sessions`` are the distinct session identifiers (any order). They are sorted and
    split into ``n_groups`` contiguous blocks; every choice of ``k_test`` blocks is a test
    set. Train excludes the test sessions AND any session within ``embargo`` positions of a
    test session. Yields ``(train_sessions, test_sessions)`` tuples."""
    if embargo < 1:
        raise ValueError("embargo must be >= 1 session (labels are same-session)")
    uniq = sorted(set(sessions))
    if not 1 <= k_test < n_groups:
        raise ValueError("require 1 <= k_test < n_groups")
    if n_groups > len(uniq):
        raise ValueError(f"n_groups {n_groups} > distinct sessions {len(uniq)}")
    pos = {s: i for i, s in enumerate(uniq)}
    blocks = [list(b) for b in np.array_split(np.arange(len(uniq)), n_groups)]
    out: list[tuple[tuple, tuple]] = []
    for combo in combinations(range(n_groups), k_test):
        test_idx = sorted(i for g in combo for i in blocks[g])
        banned = set(test_idx)
        for i in test_idx:
            for d in range(1, embargo + 1):
                banned.add(i - d)
                banned.add(i + d)
        train_idx = [i for i in range(len(uniq)) if i not in banned]
        if not train_idx or not test_idx:
            continue
        test_sessions = tuple(uniq[i] for i in test_idx)
        train_sessions = tuple(uniq[i] for i in train_idx)
        out.append((train_sessions, test_sessions))
    return out


def rows_for_sessions(df: pd.DataFrame, sessions: Sequence, col: str = "session_date") -> pd.DataFrame:
    return df[df[col].isin(set(sessions))]


def cluster_ids(df: pd.DataFrame, symbol_col: str = "symbol",
                session_col: str = "session_date") -> np.ndarray:
    return (df[symbol_col].astype(str) + "|" + df[session_col].astype(str)).to_numpy()


# ------------------------------------------------------------------ policy P&L


def _apply_sizing(preds: dict, params: PolicyParams, rt_cost: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Vectorized trade mask + size fraction matching ``EnsembleModelPolicy`` (no context)."""
    mu, sigma, p, b = preds["mu"], preds["sigma"], preds["p"], preds["b"]
    h_eff = np.maximum(params.h, 0.5 * rt_cost)
    p_low = np.array([lower_ci_probability(pi, params.n_eff, params.p_shrink_k, params.ci_z)
                      for pi in p])
    mask = (mu - params.z * sigma > h_eff) & (p_low >= params.p_min)
    if params.n_eff < params.kelly_n_threshold:
        size = np.full(len(mu), params.f_fixed, dtype=float)
    else:
        bb = np.maximum(b, 1e-6)
        f_star = p_low - (1.0 - p_low) / bb
        size = np.clip(params.kelly_fraction * f_star, 0.0, params.f_max)
    return mask, size


def effective_round_trip(
    df: pd.DataFrame, measured_col: str | None, model_col: str | None, fallback: float,
) -> np.ndarray:
    """Per-row round-trip cost: measured where present, else modeled, else ``fallback`` (F2).

    A purely modeled cost is still a real, non-zero estimate, so a row with no measured
    quote falls back to its ``cost_rt_model`` rather than to 0 (which would make the hurdle
    and the +/-50% spread stress vacuous). ``fallback`` is only used when BOTH are absent
    or non-finite for that row."""
    n = len(df)
    rt = np.full(n, float(fallback), dtype=float)
    if model_col and model_col in df.columns:
        m = df[model_col].to_numpy(dtype=float)
        rt = np.where(np.isfinite(m), m, rt)
    if measured_col and measured_col in df.columns:
        meas = df[measured_col].to_numpy(dtype=float)
        rt = np.where(np.isfinite(meas), meas, rt)
    return rt


def labeled_rows(df: pd.DataFrame, label_col: str) -> pd.DataFrame:
    """Rows with a finite label for this exit config. Session-truncated trades carry NaN
    labels (~1% of real rows); they are excluded from fitting and scoring, never imputed
    (2026-10-18 fix: the first real-data run crashed in fit on them)."""
    return df[np.isfinite(df[label_col].to_numpy(dtype=float))]


def policy_trade_returns(
    model, params: PolicyParams, test_df: pd.DataFrame, *,
    label_col: str, cost_col: str | None = None, model_cost_col: str | None = None,
    cost_mult: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Per-traded-candidate policy net return (after the cost stress) and its cluster ids.

    The stressed net return adds back ``(1 - cost_mult)`` of the round-trip cost: the
    stored label already nets a 1.0x round trip, so ``cost_mult=1.5`` subtracts an extra
    0.5x and ``cost_mult=0.5`` refunds 0.5x. The per-row round-trip is the measured spread
    where present, else the modeled spread (F2), so the stress and the hurdle never go
    vacuous for rows that only have a model cost."""
    test_df = labeled_rows(test_df, label_col)
    if test_df.empty:
        return np.array([]), np.array([])
    preds = model.predict_frame(test_df)
    y = test_df[label_col].to_numpy(dtype=float)
    rt = effective_round_trip(test_df, cost_col, model_cost_col, params.fallback_round_trip)
    y_stressed = y + (1.0 - cost_mult) * rt
    mask, size = _apply_sizing(preds, params, rt)
    clusters = cluster_ids(test_df)
    return (size[mask] * y_stressed[mask]), clusters[mask]


def fold_diagnostics(model, test_df: pd.DataFrame, label_col: str) -> dict:
    """AUC of p, Brier calibration, corr(mu, realized) on a test fold."""
    test_df = labeled_rows(test_df, label_col)
    preds = model.predict_frame(test_df)
    y = test_df[label_col].to_numpy(dtype=float)
    y_bin = (y > 0).astype(int)
    n_pos = int(y_bin.sum())
    n_neg = int(y_bin.size - n_pos)
    out = {"auc": float("nan"), "brier": float("nan"), "mu_corr": float("nan"),
           "n_pos": n_pos, "n_neg": n_neg}
    if len(np.unique(y_bin)) == 2:
        out["auc"] = float(roc_auc_score(y_bin, preds["p"]))
        out["brier"] = float(np.mean((preds["p"] - y_bin) ** 2))
    if np.std(preds["mu"]) > 0 and np.std(y) > 0:
        out["mu_corr"] = float(np.corrcoef(preds["mu"], y)[0, 1])
    return out


# ------------------------------------------------------------------ cluster bootstrap


def cluster_bootstrap_ci(
    returns: Sequence[float], clusters: Sequence, *,
    stat: Callable[[np.ndarray], float] = np.mean, n: int = 2000,
    alpha: float = 0.05, seed: int = 0,
) -> tuple[float, float]:
    """Percentile CI resampling whole (session, symbol) clusters with replacement (F4)."""
    r = np.asarray(returns, dtype=float)
    c = np.asarray(clusters)
    if r.size == 0:
        raise ValueError("cluster_bootstrap_ci requires >= 1 return")
    by_cluster: dict = {}
    for ret, cl in zip(r, c):
        by_cluster.setdefault(cl, []).append(ret)
    keys = list(by_cluster.keys())
    blocks = [np.asarray(by_cluster[k], dtype=float) for k in keys]
    rng = np.random.default_rng(seed)
    estimates = np.empty(n, dtype=float)
    for i in range(n):
        idx = rng.integers(0, len(keys), size=len(keys))
        sample = np.concatenate([blocks[j] for j in idx])
        estimates[i] = stat(sample)
    return float(np.quantile(estimates, alpha / 2.0)), float(np.quantile(estimates, 1.0 - alpha / 2.0))


# ------------------------------------------------------------------ gates / verdict


@dataclass(frozen=True, slots=True)
class Gate:
    name: str
    passed: bool
    detail: str


def sign_consistent_positive(per_fold_exp: Sequence[float]) -> bool:
    """Promotion needs OOS expectancy positive in EVERY fold, not just pooled (F6)."""
    exps = list(per_fold_exp)
    return bool(exps) and all(e > 0 for e in exps)


def cost_stress_sign_stable(exp_by_mult: dict) -> bool:
    """Reject if the expectancy sign flips (or zeroes) across the +/-50% spread stress (F1)."""
    try:
        signs = {np.sign(exp_by_mult[m]) for m in (0.5, 1.0, 1.5)}
    except KeyError:
        return False
    return len(signs) == 1 and 0.0 not in signs


def null_auc_tolerance(auc: float, n_pos: int, n_neg: int) -> float:
    """Half-width of the AUC null band, sized to the data (F5, 2026-10-18).

    A flat +/-0.10 band passes a shuffled AUC that is clearly above chance on a large
    sample. Instead the band is ``max(NULL_AUC_FLOOR, 2 * SE)`` with the Wald-style
    ``SE = sqrt(AUC*(1-AUC) / min(n_pos, n_neg))`` -- it widens for tiny samples (where
    even a clean shuffle scatters) and tightens toward the floor for large ones."""
    n = min(int(n_pos), int(n_neg))
    if n <= 0 or not np.isfinite(auc):
        return NULL_AUC_FLOOR
    se = float(np.sqrt(max(auc * (1.0 - auc), 0.0) / n))
    return max(NULL_AUC_FLOOR, 2.0 * se)


def is_null_result(auc: float, mu_corr: float, n_pos: int = 0, n_neg: int = 0) -> bool:
    """True iff a label-shuffled run scores close enough to the no-information null (F8).

    The AUC band is data-size-aware (:func:`null_auc_tolerance`, F5); pass the shuffled
    run's positive/negative class counts so the band reflects its sample size."""
    tol = null_auc_tolerance(auc, n_pos, n_neg)
    auc_ok = (not np.isfinite(auc)) or abs(auc - 0.5) <= tol
    corr_ok = (not np.isfinite(mu_corr)) or abs(mu_corr) <= NULL_CORR_TOL
    return auc_ok and corr_ok


def deflated_sharpe_gate(
    returns: Sequence[float], n_trials: int, sr_variance: float | None, *,
    threshold: float = 0.95,
) -> tuple[float, bool]:
    """Deflated Sharpe over the whole trial lineage (F5). Returns (dsr, passed)."""
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return 0.0, False
    from scipy import stats
    std = float(r.std(ddof=1))
    sr = float(r.mean()) / std if std > 0 else 0.0
    skew = float(stats.skew(r)) if r.size >= 3 else 0.0
    kurt = float(stats.kurtosis(r, fisher=False)) if r.size >= 4 else 3.0
    if sr_variance is None:
        sr_variance = (1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr ** 2) / (r.size - 1)
    dsr = deflated_sharpe_ratio(sr, max(1, n_trials), sr_variance, r.size, skew, kurt)
    return float(dsr), dsr > threshold
