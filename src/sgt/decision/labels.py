"""ATR-bracket labels for the decision model (docs/DECISION_MODEL.md §5, §10 F8).

The label of a candidate is the realized NET return of the exit policy the strategy
actually runs: enter at the next bar's open plus a half-spread, exit at the first of
a profit target at ``k_tp * ATR``, a stop at ``k_sl * ATR``, or a time limit of ``H``
bars, minus a half-spread. ATR (measured at the signal bar) scales the brackets so a
stock's own volatility sets the barriers rather than a fixed percentage.

Determinism / conservatism (matches ``sgt.learning.labels``):
- entry is the bar strictly after the signal, in the SAME session; a signal whose
  next bar is a new session gets no label (NaN, counted);
- a bar that OPENS beyond a barrier fills at that open (gap-through), not the level;
- a bar whose range touches BOTH barriers resolves as a stop (we cannot know the
  intrabar order from OHLC);
- a horizon that leaves the session before resolving is truncated -> NaN (counted).

Overlapping labels share information, so each label carries a López de Prado average
-uniqueness weight (:func:`uniqueness_weights`) the trainer uses to down-weight
concurrent, correlated samples.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sgt.core.types import Bar

_DEFAULT_K_TP = (1.0, 2.0, 3.0)
_DEFAULT_K_SL = (1.0, 1.5, 2.0)
_DEFAULT_H = (10, 20, 30)


@dataclass(frozen=True, slots=True)
class LabelConfig:
    k_tp: float
    k_sl: float
    h: int

    def __post_init__(self) -> None:
        if self.k_tp <= 0 or self.k_sl <= 0:
            raise ValueError("k_tp and k_sl must be > 0")
        if self.h <= 0:
            raise ValueError("h must be > 0")

    @property
    def suffix(self) -> str:
        return f"tp{_fmt(self.k_tp)}_sl{_fmt(self.k_sl)}_h{self.h}"


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)


def default_grid() -> tuple[LabelConfig, ...]:
    return tuple(
        LabelConfig(tp, sl, h)
        for tp in _DEFAULT_K_TP for sl in _DEFAULT_K_SL for h in _DEFAULT_H
    )


@dataclass(frozen=True, slots=True)
class BracketOutcome:
    label: int          # +1 take-profit, -1 stop, 0 timeout
    net_ret: float      # net return after both half-spreads
    bars_held: int      # bars from entry to exit (>= 1)
    entry_idx: int      # index of the entry bar in the symbol's bar list
    exit_idx: int       # index of the exit bar


def atr_bracket_label(
    bars: Sequence[Bar],
    signal_idx: int,
    atr: float | None,
    half_spread: float,
    cfg: LabelConfig,
) -> BracketOutcome | None:
    """Label one signal, or ``None`` when it cannot be labeled (truncated / no ATR).

    ``atr`` is the ATR known at the signal bar; ``half_spread`` is a per-side cost
    fraction (round-trip / 2) measured at entry. Barriers are placed off the raw
    entry fill (the next bar's open); the net return applies the half-spread on both
    legs.
    """
    if atr is None or atr <= 0.0 or half_spread < 0.0:
        return None
    entry_i = signal_idx + 1
    if entry_i >= len(bars):
        return None
    entry_bar = bars[entry_i]
    if entry_bar.session_date != bars[signal_idx].session_date:
        return None
    entry_raw = entry_bar.open
    if entry_raw <= 0.0:
        return None
    entry_net = entry_raw * (1.0 + half_spread)
    tp = entry_raw + cfg.k_tp * atr
    sl = entry_raw - cfg.k_sl * atr
    last = min(signal_idx + cfg.h, len(bars) - 1)

    for j in range(entry_i, last + 1):
        b = bars[j]
        if b.session_date != entry_bar.session_date:
            return None  # left the session before resolving -> truncated
        held = j - signal_idx
        if b.open >= tp:
            return _mk(1, entry_net, b.open, half_spread, held, entry_i, j)
        if b.open <= sl:
            return _mk(-1, entry_net, b.open, half_spread, held, entry_i, j)
        hit_tp = b.high >= tp
        hit_sl = b.low <= sl
        if hit_tp and hit_sl:
            return _mk(-1, entry_net, sl, half_spread, held, entry_i, j)  # stop first
        if hit_tp:
            return _mk(1, entry_net, tp, half_spread, held, entry_i, j)
        if hit_sl:
            return _mk(-1, entry_net, sl, half_spread, held, entry_i, j)

    if last == signal_idx + cfg.h:  # full horizon elapsed in-session -> timeout
        b = bars[last]
        return _mk(0, entry_net, b.close, half_spread, last - signal_idx, entry_i, last)
    return None  # not enough bars for a full horizon -> truncated


def _mk(label, entry_net, exit_raw, half, held, entry_i, exit_i) -> BracketOutcome:
    exit_net = exit_raw * (1.0 - half)
    return BracketOutcome(
        label=label, net_ret=exit_net / entry_net - 1.0,
        bars_held=held, entry_idx=entry_i, exit_idx=exit_i)


def uniqueness_weights(spans: Sequence[tuple[int, int]], n_bars: int) -> list[float]:
    """Average uniqueness (López de Prado) for overlapping label spans.

    ``spans[i] = (entry_idx, exit_idx)`` inclusive. Concurrency ``c_t`` is the number
    of spans covering bar ``t``; a span's weight is the mean of ``1/c_t`` over its
    bars. Non-overlapping labels weight 1.0; fully concurrent labels weight down
    toward ``1/k``. Empty-span safety returns ``nan``.
    """
    conc = [0] * n_bars
    for a, b in spans:
        for t in range(a, b + 1):
            if 0 <= t < n_bars:
                conc[t] += 1
    out: list[float] = []
    for a, b in spans:
        total = 0.0
        count = 0
        for t in range(a, b + 1):
            if 0 <= t < n_bars and conc[t] > 0:
                total += 1.0 / conc[t]
                count += 1
        out.append(total / count if count else float("nan"))
    return out
