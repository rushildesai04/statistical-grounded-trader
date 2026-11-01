"""Daily ATR-bracket labels for swing trades (A, 2026-10-18).

Signal at day ``i``'s close; entry at day ``i+1``'s OPEN. Barriers from the signal-day ATR:
target ``entry + k_tp*ATR``, stop ``entry - k_sl*ATR``. Days ``i+1 .. i+H`` are scanned in
order; on each day the stop is checked FIRST (a day whose range touches both resolves as a
stop -- the daily bar cannot order them). Fills: a day opening beyond a barrier fills at the
open (gap), otherwise at the barrier. Untouched -> exit at day ``i+H``'s close. A horizon
running past the data -> NaN (truncated, never imputed). Net return pays the per-side
half-spread on entry and exit.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class SwingLabel:
    k_tp: float
    k_sl: float
    h: int

    @property
    def suffix(self) -> str:
        f = lambda x: f"{x:g}"  # noqa: E731
        return f"tp{f(self.k_tp)}_sl{f(self.k_sl)}_h{self.h}"


def default_swing_grid() -> tuple[SwingLabel, ...]:
    return tuple(SwingLabel(tp, sl, h) for tp in (1.0, 2.0, 3.0) for sl in (1.0, 2.0)
                 for h in (3, 5, 10))


def bracket_labels(o, h, l, c, atr, idx, cfg: SwingLabel, half_spread):
    """Labels for signal rows ``idx`` of one symbol's arrays.

    Returns (net, exit_offset) arrays aligned with ``idx`` (NaN where unlabeled).
    ``half_spread`` is the per-side cost fraction per signal row.
    """
    n = len(c)
    idx = np.asarray(idx, dtype=int)
    net = np.full(len(idx), np.nan)
    held = np.full(len(idx), np.nan)
    ok = (idx + cfg.h < n) & np.isfinite(atr[idx]) & (atr[idx] > 0)
    for k in np.flatnonzero(ok):
        i = idx[k]
        entry = o[i + 1]
        if not entry > 0:
            continue
        tp, sl = entry + cfg.k_tp * atr[i], entry - cfg.k_sl * atr[i]
        exit_px, off = c[i + cfg.h], cfg.h
        for j in range(1, cfg.h + 1):
            d = i + j
            if l[d] <= sl:
                exit_px = o[d] if (j > 1 and o[d] < sl) else sl
                off = j
                break
            if h[d] >= tp:
                exit_px = o[d] if (j > 1 and o[d] > tp) else tp
                off = j
                break
        hs = half_spread[k]
        net[k] = (exit_px * (1.0 - hs)) / (entry * (1.0 + hs)) - 1.0
        held[k] = off
    return net, held
