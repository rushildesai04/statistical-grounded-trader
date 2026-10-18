"""Decision policy: turn model outputs (mu, sigma, p, b) into a trade + size.

The ``PolicyDecision`` dataclass and the ``ModelPolicy`` protocol are the contract
the ``DecisionRuntime`` (owned by the dataset/runtime workstream) imports from here;
the field names are load-bearing and must not change.

Policy rule (DECISION_MODEL.md §7, §10 F7):
- Trade iff ``mu - z*sigma > h_eff`` AND ``p_lowCI >= p_min`` and no hard veto, where
  ``h_eff = max(h, 0.5 * measured round-trip cost)`` (F1: the hurdle is at least half
  the measured round trip until live fills calibrate it).
- ``p`` is shrunk toward 0.5 by estimation-error weight and Kelly uses the LOWER CI of
  ``p``; ``b`` is shrunk toward the global ratio inside the model.
- Sizing is fixed-fractional until N >= ``kelly_n_threshold`` clusters, then shrunk
  quarter-Kelly on ``p_lowCI`` clipped to ``f_max``.
- A portfolio at-risk cap applies across correlated open positions: the context
  reports the fraction of allocation already at risk; the new position may not push the
  total above ``portfolio_cap``.

NO-TRADE is a first-class result (F9): every rejection returns ``trade=False`` with a
``reason`` naming the gate that failed, never an exception.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from math import sqrt
from typing import Mapping, Protocol, Sequence, runtime_checkable

# ``y_net__tp{k_tp}_sl{k_sl}_h{H}`` -- the dataset label column the model was trained on.
_LABEL_RE = re.compile(r"^y_net__tp([0-9.]+)_sl([0-9.]+)_h([0-9]+)$")


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """One decision for one candidate. Field names are a public contract."""

    trade: bool
    size_fraction: float
    stop_price: float | None
    take_profit_price: float | None
    reason: str
    contributions: list[tuple[str, float]] = field(default_factory=list)


@runtime_checkable
class ModelPolicy(Protocol):
    """Maps point-in-time features + runtime context to a sized decision."""

    def decide(self, features: Mapping[str, float], context: object) -> PolicyDecision: ...


@dataclass(frozen=True, slots=True)
class PolicyParams:
    """Tuned on training folds only (DECISION_MODEL.md §7)."""

    k_tp: float
    k_sl: float
    n_eff: int
    z: float = 1.0
    h: float = 0.0
    p_min: float = 0.5
    f_max: float = 0.02
    f_fixed: float = 0.005
    kelly_fraction: float = 0.25
    kelly_n_threshold: int = 1000
    portfolio_cap: float = 0.06
    p_shrink_k: float = 50.0
    ci_z: float = 1.0
    fallback_round_trip: float = 0.0

    def as_dict(self) -> dict:
        return {
            "k_tp": self.k_tp, "k_sl": self.k_sl, "n_eff": self.n_eff, "z": self.z,
            "h": self.h, "p_min": self.p_min, "f_max": self.f_max, "f_fixed": self.f_fixed,
            "kelly_fraction": self.kelly_fraction, "kelly_n_threshold": self.kelly_n_threshold,
            "portfolio_cap": self.portfolio_cap, "p_shrink_k": self.p_shrink_k,
            "ci_z": self.ci_z, "fallback_round_trip": self.fallback_round_trip,
        }


def parse_label_config(label_col: str) -> tuple[float, float, int]:
    """Parse ``(k_tp, k_sl, H)`` out of a ``y_net__tp*_sl*_h*`` label column name."""
    m = _LABEL_RE.match(label_col or "")
    if not m:
        raise ValueError(
            f"model artifact label_col {label_col!r} is not a y_net__tp*_sl*_h* name; "
            "cannot verify exit-bracket consistency")
    return float(m.group(1)), float(m.group(2)), int(m.group(3))


def validate_exit_consistency(bracket, params: "PolicyParams", model_metadata: Mapping) -> None:
    """Fail closed if the exit bracket, policy k values and trained label config disagree.

    F4 (DECISION_MODEL.md §10): the runtime's ATR brackets must match the barriers the
    model was trained and sized against, or every exit resolves on levels the model never
    optimized. ``bracket`` is an ``ExitBracket`` (duck-typed: ``k_tp``, ``k_sl``,
    ``time_stop_minutes``). The label config ``(k_tp, k_sl, H)`` is read from the model
    artifact metadata's ``label_col``; ``H`` is a bar count and the engine runs 1-minute
    bars, so it must equal ``time_stop_minutes``. Any mismatch raises ``ValueError``.
    """
    lbl_tp, lbl_sl, lbl_h = parse_label_config(str(model_metadata.get("label_col", "")))
    problems: list[str] = []
    if not _close(bracket.k_tp, lbl_tp) or not _close(params.k_tp, lbl_tp):
        problems.append(
            f"k_tp mismatch: bracket={bracket.k_tp}, policy={params.k_tp}, label={lbl_tp}")
    if not _close(bracket.k_sl, lbl_sl) or not _close(params.k_sl, lbl_sl):
        problems.append(
            f"k_sl mismatch: bracket={bracket.k_sl}, policy={params.k_sl}, label={lbl_sl}")
    if int(bracket.time_stop_minutes) != lbl_h:
        problems.append(
            f"time-stop mismatch: bracket.time_stop_minutes={bracket.time_stop_minutes} "
            f"!= label H={lbl_h} bars")
    if problems:
        raise ValueError(
            "model-strategy exit bracket is inconsistent with the trained label config "
            "(F4): " + "; ".join(problems))


def _close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= 1e-9


def shrink_probability(p: float, n_eff: int, k: float) -> float:
    """Shrink a calibrated probability toward 0.5 by estimation-error weight.

    weight = n_eff / (n_eff + k): with little data the estimate is pulled to the
    no-information prior; with much data it is trusted. Clipped to (0, 1)."""
    n = max(int(n_eff), 0)
    w = n / (n + k) if (n + k) > 0 else 0.0
    ps = 0.5 + w * (float(p) - 0.5)
    return min(1.0 - 1e-9, max(1e-9, ps))


def lower_ci_probability(p: float, n_eff: int, k: float, z: float) -> float:
    """Lower confidence bound of the shrunk win probability (Wald on n_eff clusters)."""
    ps = shrink_probability(p, n_eff, k)
    n = max(int(n_eff), 1)
    se = sqrt(ps * (1.0 - ps) / n)
    return max(0.0, ps - z * se)


def _num(context: object, *names: str) -> float | None:
    """First finite numeric field found under any of ``names`` (mapping or attribute style).

    Aliases let the policy read either the brief's field names (``mark_price``,
    ``round_trip_cost``) or the ``DecisionRuntime``'s ``PolicyContext`` equivalents
    (``close``, ``cost_frac``) without coupling to one shape."""
    for name in names:
        val = context.get(name) if isinstance(context, Mapping) else getattr(context, name, None)
        if val is None:
            continue
        try:
            f = float(val)
        except (TypeError, ValueError):
            continue
        if f == f:  # reject NaN
            return f
    return None


class EnsembleModelPolicy:
    """Concrete ``ModelPolicy`` over a fitted ``DecisionModel`` + ``PolicyParams``.

    The ``context`` must expose (attribute or mapping key): ``mark_price`` (entry
    reference, > 0), ``atr`` (> 0, for the ATR brackets), ``round_trip_cost`` (fractional
    measured round trip; falls back to ``params.fallback_round_trip``) and ``open_risk``
    (fraction of allocation already at risk across open positions)."""

    def __init__(self, model, params: PolicyParams) -> None:
        self._model = model
        self._p = params

    @property
    def params(self) -> PolicyParams:
        """Policy params (read by the runtime's F4 exit-consistency check)."""
        return self._p

    @property
    def model_metadata(self) -> Mapping:
        """Fitted-model artifact metadata (carries the trained ``label_col`` for F4)."""
        return getattr(self._model, "metadata", {}) or {}

    def decide(self, features: Mapping[str, float], context: object) -> PolicyDecision:
        p = self._p
        pred = self._model.predict(features)
        contribs = list(pred.contributions)

        rt = _num(context, "round_trip_cost", "cost_frac")
        if rt is None:
            rt = p.fallback_round_trip
        h_eff = max(p.h, 0.5 * rt)

        edge = pred.mu - p.z * pred.sigma
        p_low = lower_ci_probability(pred.p, p.n_eff, p.p_shrink_k, p.ci_z)

        if edge <= h_eff:
            return PolicyDecision(
                False, 0.0, None, None,
                f"no-trade: edge {edge:.5f} <= h_eff {h_eff:.5f} "
                f"(mu={pred.mu:.5f}, z*sigma={p.z * pred.sigma:.5f})", contribs)
        if p_low < p.p_min:
            return PolicyDecision(
                False, 0.0, None, None,
                f"no-trade: p_lowCI {p_low:.4f} < p_min {p.p_min:.4f} (p={pred.p:.4f})",
                contribs)

        # Sizing: fixed-fractional below the cluster threshold (F7), else shrunk 1/4-Kelly.
        if p.n_eff < p.kelly_n_threshold:
            size = p.f_fixed
            size_note = f"fixed-fractional (N={p.n_eff} < {p.kelly_n_threshold})"
        else:
            b = max(pred.b, 1e-6)
            f_star = p_low - (1.0 - p_low) / b
            size = min(max(p.kelly_fraction * f_star, 0.0), p.f_max)
            size_note = f"1/4-Kelly(p_low={p_low:.4f}, b={b:.3f})={size:.4f}"

        open_risk = _num(context, "open_risk") or 0.0
        headroom = max(0.0, p.portfolio_cap - open_risk)
        if headroom <= 0.0:
            return PolicyDecision(
                False, 0.0, None, None,
                f"no-trade: portfolio at-risk cap reached (open_risk={open_risk:.4f} "
                f">= cap {p.portfolio_cap:.4f})", contribs)
        size = min(size, headroom)
        if size <= 0.0:
            return PolicyDecision(
                False, 0.0, None, None, f"no-trade: size collapsed to 0 ({size_note})",
                contribs)

        entry = _num(context, "mark_price", "close")
        atr = _num(context, "atr")
        stop_price = tp_price = None
        if entry is not None and entry > 0 and atr is not None and atr > 0:
            stop_price = entry - p.k_sl * atr
            tp_price = entry + p.k_tp * atr

        reason = (f"trade: edge {edge:.5f} > h_eff {h_eff:.5f}, p_lowCI {p_low:.4f} "
                  f">= {p.p_min:.4f}; size {size:.4f} [{size_note}, "
                  f"headroom {headroom:.4f}]")
        return PolicyDecision(True, float(size), stop_price, tp_price, reason, contribs)


def sized_returns(
    decisions: Sequence[PolicyDecision], net_returns: Sequence[float]
) -> list[float]:
    """Per-candidate policy P&L: ``size_fraction * net_return`` for traded candidates.

    Candidates the policy declined contribute nothing (NO-TRADE earns 0, not a loss)."""
    if len(decisions) != len(net_returns):
        raise ValueError("decisions and net_returns must align")
    out: list[float] = []
    for d, r in zip(decisions, net_returns):
        if d.trade:
            out.append(d.size_fraction * float(r))
    return out
