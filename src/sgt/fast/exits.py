"""Fade exits that read the tape (prereg_fade_exits_2026-11-05). Pure functions.

``bars``: one session's RTH 1-minute bars (``ts`` UTC, open/high/low/close/volume), sorted. A signal is read at a
bar's close and acted on at the next bar's open; the 20% stop is checked first inside every bar (worst case).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

STOP = 0.20


@dataclass(frozen=True, slots=True)
class Exit:
    id: str
    signal: str | None = None          # None (time only) | trail | vwap | ema | target
    cap_min: int = 60
    stop: float = STOP


CHAMPION = Exit("champion")
EXITS = (Exit("X-TRAIL", "trail", 120), Exit("X-VWAP", "vwap", 120), Exit("X-EMA", "ema", 120),
         Exit("X-TGT", "target", 60))
HOLDS = (Exit("H120", None, 120), Exit("H-CLOSE", None, 480))     # prereg_fade_hold_2026-11-05; RTH bars end at 16:00
TRAIL_ARM, TRAIL_GIVE, TARGET = 0.05, 0.05, 0.15


def indicators(bars: pd.DataFrame) -> pd.DataFrame:
    """Session VWAP from the first RTH bar and EMA 9 / 21 of RTH closes, each known at that bar's close."""
    b = bars.copy()
    tp = (b.high + b.low + b.close) / 3
    vol = b.volume.astype(float)
    cum = vol.cumsum()
    b["vwap"] = (tp * vol).cumsum() / cum.where(cum > 0)
    b["ema9"] = b.close.ewm(span=9, adjust=False).mean()
    b["ema21"] = b.close.ewm(span=21, adjust=False).mean()
    return b


def simulate(ind: pd.DataFrame, entry_ts: pd.Timestamp, entry: float, x: Exit) -> dict | None:
    """Gross return of the short and how it ended; None = no RTH bar to hold."""
    pos = np.flatnonzero((ind.ts >= entry_ts).to_numpy())
    if not len(pos):
        return None
    i0 = int(pos[0])
    end = ind.ts < entry_ts + pd.Timedelta(minutes=x.cap_min)
    last = int(np.flatnonzero(end.to_numpy())[-1]) if end.any() else -1
    if last < i0:
        return None
    o, h, lo, c = (ind[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    vw, e9, e21 = (ind[k].to_numpy(float) for k in ("vwap", "ema9", "ema21"))
    lvl = entry * (1 + x.stop)
    low, below, armed = np.inf, False, False

    def done(px: float, how: str, i: int) -> dict:
        return {"gross": (entry - px) / entry, "how": how, "minutes": i - i0 + 1}

    for i in range(i0, last + 1):
        if h[i] >= lvl:
            return done(max(lvl, o[i]), "stop", i)
        if x.signal == "target" and lo[i] <= entry * (1 - TARGET):
            return done(min(entry * (1 - TARGET), o[i]), "signal", i)
        low = min(low, lo[i])
        fire = False
        if x.signal == "trail":
            armed = armed or low <= entry * (1 - TRAIL_ARM)
            fire = armed and c[i] >= low * (1 + TRAIL_GIVE)
        elif x.signal == "vwap" and not np.isnan(vw[i]):
            fire = below and c[i] > vw[i]
            below = below or c[i] < vw[i]
        elif x.signal == "ema" and i > 0:
            fire = e9[i] > e21[i] and e9[i - 1] <= e21[i - 1]
        if fire:
            return done(o[i + 1], "signal", i + 1) if i < last else done(c[i], "signal", i)
    return done(c[last], "cap", last)
