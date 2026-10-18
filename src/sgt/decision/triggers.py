"""Candidate triggers for the decision model (docs/DECISION_MODEL.md §3).

A candidate is raised on a bar close when any of the six triggers fires AND the
symbol is flat. Which trigger fired is itself a feature (one-hot), so each trigger
has a canonical name. Asking the model at *every* bar is rejected by design (it
multiplies correlated near-duplicate samples); the triggers are the sampling gate.

All triggers are pure functions of an :class:`sgt.strategy.base.EvalView` and read
only point-in-time inputs (current + previous snapshot, bounded history, the
session-high trackers already maintained by the engine). A missing/warm-up input
makes a trigger ``False`` — never raises. :func:`fired_trigger` returns the single
highest-priority trigger that fired (deterministic), so one bar yields at most one
candidate row even when several triggers coincide.
"""
from __future__ import annotations

from collections.abc import Callable

from sgt.core.interfaces import IndicatorSpec
from sgt.strategy.base import EvalView

_EMA_FAST = IndicatorSpec.of("ema", n=9)
_EMA_SLOW = IndicatorSpec.of("ema", n=24)
_MACD = IndicatorSpec.of("macd", fast=12, slow=26, signal=9)
_VWAP = IndicatorSpec.of("vwap", anchor="session")
_PSAR = IndicatorSpec.of("psar")

_K_EMA_FAST = f"{_EMA_FAST.key}.value"
_K_EMA_SLOW = f"{_EMA_SLOW.key}.value"
_K_MACD_LINE = f"{_MACD.key}.line"
_K_MACD_SIG = f"{_MACD.key}.signal"
_K_VWAP = f"{_VWAP.key}.value"
_K_PSAR_TREND = f"{_PSAR.key}.trend"


def trigger_indicator_specs() -> list[IndicatorSpec]:
    """Indicator specs the triggers read (a subset of the feature specs)."""
    return [_EMA_FAST, _EMA_SLOW, _MACD, _VWAP, _PSAR]


def _ema_cross_up(view: EvalView) -> bool:
    fn, sn = view.now(_K_EMA_FAST), view.now(_K_EMA_SLOW)
    fp, sp = view.prev(_K_EMA_FAST), view.prev(_K_EMA_SLOW)
    if None in (fn, sn, fp, sp):
        return False
    return fn > sn and fp <= sp


def _macd_cross_up(view: EvalView) -> bool:
    ln, sn = view.now(_K_MACD_LINE), view.now(_K_MACD_SIG)
    lp, sp = view.prev(_K_MACD_LINE), view.prev(_K_MACD_SIG)
    if None in (ln, sn, lp, sp):
        return False
    return ln > sn and lp <= sp


def _vwap_reclaim(view: EvalView) -> bool:
    vn, vp = view.now(_K_VWAP), view.prev(_K_VWAP)
    if vn is None or vp is None or not len(view.history):
        return False
    prev_close = view.history.closes[-1]
    return view.bar.close > vn and prev_close <= vp


def _hod_break(view: EvalView) -> bool:
    hi = view.session_high_prior
    return hi is not None and view.bar.close > hi


def _pmh_break(view: EvalView) -> bool:
    hi = view.premarket_high
    return hi is not None and view.bar.close > hi


def _psar_flip_up(view: EvalView) -> bool:
    n, p = view.now(_K_PSAR_TREND), view.prev(_K_PSAR_TREND)
    if n is None or p is None:
        return False
    return n > 0 and p < 0


# Priority order: the one-hot of the first fired wins when several coincide.
TRIGGERS: dict[str, Callable[[EvalView], bool]] = {
    "ema_cross_up": _ema_cross_up,
    "macd_cross_up": _macd_cross_up,
    "vwap_reclaim": _vwap_reclaim,
    "hod_break": _hod_break,
    "pmh_break": _pmh_break,
    "psar_flip_up": _psar_flip_up,
}

TRIGGER_NAMES: tuple[str, ...] = tuple(TRIGGERS)


def fired_trigger(view: EvalView, enabled: frozenset[str] | None = None) -> str | None:
    """The single highest-priority enabled trigger that fires on this bar, or None.

    ``enabled`` restricts to a strategy's configured trigger subset (default: all)."""
    for name, fn in TRIGGERS.items():
        if enabled is not None and name not in enabled:
            continue
        if fn(view):
            return name
    return None
