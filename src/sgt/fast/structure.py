"""Swing-pivot market structure: break of structure (BoS) and change of character (CHoCH).

prereg_bos_ema_2026-11-13 (owner 2026-11-12 night: "a BoS while 9 is above 21 ... nowhere to go but up"). Standard
definition, pivot length 5; frozen at the study commit. Causal: the state at bar i uses bars <= i only (a pivot at
bar j is usable from bar j + LEN)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

LEN = 5


@dataclass(frozen=True, slots=True)
class Break:
    i: int                      # bar whose close broke the level
    kind: str                   # "BOS" | "CHOCH" (bullish breaks only are returned)
    level: float                # the pivot high that was broken
    stop: float | None          # the most recent confirmed pivot low at bar i (None = none yet)


def pivots(high: np.ndarray, low: np.ndarray, n: int = LEN) -> tuple[np.ndarray, np.ndarray]:
    """Boolean arrays: bar j is a pivot high (strictly above the n highs before, >= the n highs after) / pivot low
    (strictly below the n lows before, <= the n lows after). Bars without n bars on both sides are never pivots."""
    m = len(high)
    ph, pl = np.zeros(m, bool), np.zeros(m, bool)
    for j in range(n, m - n):
        ph[j] = high[j] > high[j - n:j].max() and high[j] >= high[j + 1:j + n + 1].max()
        pl[j] = low[j] < low[j - n:j].min() and low[j] <= low[j + 1:j + n + 1].min()
    return ph, pl


def bullish_breaks(f: pd.DataFrame, n: int = LEN, owner: bool = False) -> list[Break]:
    """Every bullish break in time order. Bearish breaks only update the trend. Both on one bar: bearish first.
    ``owner=True`` (prereg A1, the owner's Pine script): a bullish break is a CHoCH only when the trend is down (so the
    day's first break is a BOS), and on a bar that breaks both levels the bullish break is applied first."""
    high, low, close = (f[c].to_numpy(float) for c in ("high", "low", "close"))
    ph, pl = pivots(high, low, n)
    out: list[Break] = []
    last_high = last_low = stop = None
    trend = "none"
    for i in range(len(f)):
        j = i - n                                   # the pivot confirmed by this bar's close
        if j >= 0:
            if ph[j]:
                last_high = float(high[j])
            if pl[j]:
                last_low = stop = float(low[j])
        bear = last_low is not None and close[i] < last_low
        if bear and not owner:
            trend, last_low = "down", None
        if last_high is not None and close[i] > last_high:
            bos = trend != "down" if owner else trend == "up"
            out.append(Break(i, "BOS" if bos else "CHOCH", last_high, stop))
            trend, last_high = "up", None
        if bear and owner:
            trend, last_low = "down", None
    return out
