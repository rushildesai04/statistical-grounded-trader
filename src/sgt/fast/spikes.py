"""Self-detected attention spikes (prereg_self_fade_2026-11-06). Pure functions.

``rth``: one session's regular-session 1-minute bars (``ts`` UTC bar start, open/high/low/close/volume), sorted. A spike
is read at a bar's CLOSE; the short enters at the next bar's open (no look-ahead).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RUN, VOL_MULT, LOOK, BASE, MIN_BASE = 0.05, 5.0, 5, 20, 10
PRICE_MIN, PRICE_MAX = 1.0, 50.0
FIRST_ENTRY_MIN, LAST_ENTRY_MIN = 9 * 60 + 35, 15 * 60 + 44      # ET minute of the ENTRY bar


def detect(rth: pd.DataFrame, run: float = RUN, vol_mult: float = VOL_MULT, need_hod: bool = True) -> int | None:
    """Index of the first spike bar t: 5-min run-up >= ``run``, volume >= ``vol_mult`` x the prior 20-bar mean (>= 10
    prior bars), a new high of day (optional), price $1-50, entry bar t+1 inside 09:35-15:44 ET. None = no spike."""
    n = len(rth)
    if n < LOOK + 2:
        return None
    c, h, v = (rth[k].to_numpy(float) for k in ("close", "high", "volume"))
    et = pd.to_datetime(rth.ts, utc=True).dt.tz_convert("America/New_York")
    minute = (et.dt.hour * 60 + et.dt.minute).to_numpy()
    prior_high = np.maximum.accumulate(np.concatenate([[-np.inf], h[:-1]]))   # highest high BEFORE bar t
    for t in range(LOOK, n - 1):
        if not FIRST_ENTRY_MIN <= minute[t + 1] <= LAST_ENTRY_MIN:
            continue
        if not PRICE_MIN <= c[t] <= PRICE_MAX or c[t - LOOK] <= 0 or c[t] < c[t - LOOK] * (1 + run):
            continue
        base = v[max(0, t - BASE):t]
        if len(base) < MIN_BASE or base.mean() <= 0 or v[t] < vol_mult * base.mean():
            continue
        if need_hod and not h[t] > prior_high[t]:
            continue
        return t
    return None
