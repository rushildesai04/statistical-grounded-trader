"""Promotion gate for meta-filters / strategies (plan §9.2).

A candidate is promoted only if it beats, out-of-sample and net of costs, every
reference it is held against: the take-every-candidate policy, the rules-only
baseline (``trader_v0``), and the incumbent champion (when one exists). "Beats"
means the candidate's mean per-trade net return exceeds the reference's by a
statistically credible margin — the 95% bootstrap CI of the difference in means
has a lower bound strictly above zero. An in-sample-only or merely-higher-but-
noisy edge does not promote.

Inputs are mappings carrying a ``returns`` sequence of out-of-sample per-trade net
returns. ``baselines`` MUST include at least ``take_every`` and ``trader_v0``; any
additional entries are also required to be beaten.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from sgt.learning.validation import bootstrap_ci

REQUIRED_BASELINES = ("take_every", "trader_v0")


@dataclass(frozen=True, slots=True)
class Comparison:
    opponent: str
    candidate_mean: float
    opponent_mean: float
    diff_ci: tuple[float, float]
    beats: bool


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    promote: bool
    comparisons: tuple[Comparison, ...]
    reasons: tuple[str, ...]


def _returns(metrics: Mapping, who: str) -> np.ndarray:
    if "returns" not in metrics:
        raise KeyError(f"{who} metrics must contain a 'returns' sequence")
    return np.asarray(metrics["returns"], dtype=float)


def _ci_vs_zero(candidate: np.ndarray, n: int, alpha: float, seed: int) -> tuple[float, float]:
    """Bootstrap CI of mean(candidate) — the comparison against a policy that does not
    trade (an opponent with < 2 trades has no estimable mean; 'not trading' earns 0)."""
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, candidate.size, size=(n, candidate.size))
    means = candidate[idx].mean(axis=1)
    return float(np.quantile(means, alpha / 2.0)), float(np.quantile(means, 1.0 - alpha / 2.0))


def _diff_ci(
    candidate: np.ndarray, opponent: np.ndarray, n: int, alpha: float, seed: int
) -> tuple[float, float]:
    """Two-sample bootstrap CI of mean(candidate) - mean(opponent) (independent resampling)."""
    rng = np.random.default_rng(seed)
    c_idx = rng.integers(0, candidate.size, size=(n, candidate.size))
    o_idx = rng.integers(0, opponent.size, size=(n, opponent.size))
    diffs = candidate[c_idx].mean(axis=1) - opponent[o_idx].mean(axis=1)
    lo = float(np.quantile(diffs, alpha / 2.0))
    hi = float(np.quantile(diffs, 1.0 - alpha / 2.0))
    return lo, hi


def promotion_gate(
    candidate_metrics: Mapping,
    baselines: Mapping[str, Mapping],
    champion_metrics: Mapping | None,
    *,
    n_boot: int = 2000,
    alpha: float = 0.05,
    seed: int = 0,
) -> PromotionDecision:
    """Decide whether the candidate may be promoted. See module docstring."""
    missing = [b for b in REQUIRED_BASELINES if b not in baselines]
    if missing:
        raise KeyError(f"baselines must include {missing}")

    cand = _returns(candidate_metrics, "candidate")
    if cand.size < 2:
        # Insufficient evidence is a HOLD, not a crash (2026-10-13, F-13).
        return PromotionDecision(
            promote=False, comparisons=(),
            reasons=(f"insufficient evidence: candidate has {cand.size} out-of-sample trade(s), need >= 2",))
    opponents: list[tuple[str, np.ndarray]] = [
        (name, _returns(metrics, name)) for name, metrics in baselines.items()
    ]
    if champion_metrics is not None:
        opponents.append(("champion", _returns(champion_metrics, "champion")))

    comparisons: list[Comparison] = []
    reasons: list[str] = []
    for i, (name, opp) in enumerate(opponents):
        if opp.size < 2:
            lo, hi = _ci_vs_zero(cand, n_boot, alpha, seed + i)
            opp_mean = 0.0
            note = f" ({name} has {opp.size} trade(s); compared against not trading)"
        else:
            lo, hi = _diff_ci(cand, opp, n_boot, alpha, seed + i)
            opp_mean = float(opp.mean())
            note = ""
        beats = lo > 0.0
        comparisons.append(Comparison(
            opponent=name, candidate_mean=float(cand.mean()), opponent_mean=opp_mean,
            diff_ci=(lo, hi), beats=beats,
        ))
        verb = "beats" if beats else "does not beat"
        reasons.append(f"{verb} {name}: Δmean {cand.mean()-opp_mean:+.5f}, "
                       f"95% CI [{lo:+.5f}, {hi:+.5f}]{note}")

    promote = all(c.beats for c in comparisons)
    return PromotionDecision(promote=promote, comparisons=tuple(comparisons), reasons=tuple(reasons))
