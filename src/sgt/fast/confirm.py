"""Confirmation entry and failed-pullback exit (prereg_fade_confirm_2026-11-06; RAYA review). Pure functions.

``ind``: one session's RTH 1-minute bars with ``sgt.fast.exits.indicators`` columns, sorted. Signals are read at a bar's
close and acted on at the next bar's open; the 20% stop is checked first inside every bar.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.fast.exits import CHAMPION, simulate

BREAK, WAIT_MIN, PULLBACK = 0.03, 20, 0.03


def confirm_entry(ind: pd.DataFrame, entry_ts: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
    """E-CONFIRM: after a close >= 3% below the running high H (from the champion's entry bar), the first red bar whose
    high stays below H is the failed bounce; enter at the next bar's open. None = no failed bounce within 20 minutes."""
    ts = ind.ts
    pos = np.flatnonzero((ts >= entry_ts).to_numpy())
    if not len(pos):
        return None
    o, h, c = (ind[k].to_numpy(float) for k in ("open", "high", "close"))
    end = entry_ts + pd.Timedelta(minutes=WAIT_MIN)
    hi, broke = -np.inf, False
    for i in range(int(pos[0]), len(ind) - 1):
        if ts.iloc[i] >= end:
            return None
        if broke and h[i] < hi and c[i] < o[i]:
            return ts.iloc[i + 1], float(o[i + 1])
        hi = max(hi, h[i])
        broke = broke or c[i] <= hi * (1 - BREAK)
    return None


def confirm_gross(ind: pd.DataFrame, entry_ts: pd.Timestamp) -> float | None:
    """Gross of E-CONFIRM: the champion's stop and 60-minute hold from the confirmed entry; None = no trade."""
    e = confirm_entry(ind, entry_ts)
    if e is None:
        return None
    s = simulate(ind, e[0], e[1], CHAMPION)
    return None if s is None else s["gross"]


def failpb_gross(ind: pd.DataFrame, entry_ts: pd.Timestamp, entry: float) -> tuple[float, bool] | None:
    """X-FAILPB: champion entry; after a close >= 3% below the entry, cover at the next open the first time a close
    exceeds the post-entry high. Returns (gross, covered_early); otherwise the champion's result."""
    champ = simulate(ind, entry_ts, entry, CHAMPION)
    if champ is None:
        return None
    ts = ind.ts
    i0 = int(np.flatnonzero((ts >= entry_ts).to_numpy())[0])
    exit_i = i0 + champ["minutes"] - 1                 # the champion's exit bar (stop or time)
    o, h, c = (ind[k].to_numpy(float) for k in ("open", "high", "close"))
    hp, pulled = h[i0], c[i0] <= entry * (1 - PULLBACK)      # high so far (before the bar being read)
    for i in range(i0 + 1, exit_i):                    # a signal must act before the champion's own exit bar
        if pulled and c[i] > hp:
            return (entry - o[i + 1]) / entry, True
        hp, pulled = max(hp, h[i]), pulled or c[i] <= entry * (1 - PULLBACK)
    return champ["gross"], False


UNDER, BE = 0.05, 0.01


def breakeven_gross(ind: pd.DataFrame, entry_ts: pd.Timestamp, entry: float) -> tuple[float, bool] | None:
    """X-BE (owner's review 2026-11-06): once a close is >= 5% above the entry (underwater), cover at the next open the
    first time a close is back at or below entry x 1.01. Returns (gross, covered_early); otherwise the champion's."""
    champ = simulate(ind, entry_ts, entry, CHAMPION)
    if champ is None:
        return None
    i0 = int(np.flatnonzero((ind.ts >= entry_ts).to_numpy())[0])
    exit_i = i0 + champ["minutes"] - 1
    o, c = ind.open.to_numpy(float), ind.close.to_numpy(float)
    under = False
    for i in range(i0, exit_i):
        if under and c[i] <= entry * (1 + BE):
            return (entry - o[i + 1]) / entry, True
        under = under or c[i] >= entry * (1 + UNDER)
    return champ["gross"], False
