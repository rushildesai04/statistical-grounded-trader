"""Faithful encoding of the trader's own rules, from his walkthrough (2026-10-19).

Quoted from the transcript (docs: the user's pasted session, 2026-10-13):
* timing  -- "I almost always never trade in the opening 1 hour. I wait for the power hour
             only. Almost 90% of my trades happen in power hour." (+ some after-hours news)
* entry   -- "very first thing I look at is 9/21 cross" with EMA 9/24 on 1-minute candles;
             "look at how much below 9 was ... the bigger the gap ... the bigger the gains";
             "volume doesn't generate signal on its own. It confirms": 921 cross + high volume;
             examples spike ~9x (88k -> 785k shares) and turn over a large share of the float;
             PVT (vs its SMA 21) turning green after thinning; MACD histogram crossing up;
             RSI as an anti-chase filter ("don't buy when it's overbought").
* exit    -- MACD histogram "darker green ... changed to lighter green ... think about
             exiting"; RSI saturating then dropping back below 70 = reversal; volume
             exhaustion (price rising while volume falls); 9 below 21 = "world of pain".
             "Buy here, exit, wait, enter again ... rinse and repeat."
* sizing  -- ~$2,000 positions; target ~1% of the account per day.

Everything is point-in-time on 1-minute bars: a signal at bar t's close fills at t+1's open.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True, slots=True)
class TraderRules:
    window: tuple[int, int] = (15 * 60, 15 * 60 + 50)   # ET minute-of-day [start, end]
    prior_gap_pct: float = 0.005    # EMA9 was >= this far below EMA24 within the lookback
    prior_lookback: int = 15
    vol_spike: float = 5.0          # bar volume vs mean of prior 10 bars ...
    spike_lookback: int = 5         # ... on the cross bar or within the bars before it
                                    # ("volume did increase ... then 9 and 24 caught up")
    require_pvt_green: bool = True
    require_macd_pos: bool = True
    rsi_max: float = 70.0


def indicators(day: pd.DataFrame) -> pd.DataFrame:
    """Trailing 1-minute indicators over one session (all segments, time-ordered)."""
    c, h, l, v = day["close"], day["high"], day["low"], day["volume"]
    out = pd.DataFrame(index=day.index)
    out["ema9"] = c.ewm(span=9, adjust=False).mean()
    out["ema24"] = c.ewm(span=24, adjust=False).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    out["hist"] = macd - macd.ewm(span=9, adjust=False).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    out["rsi"] = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    pvt = (c.pct_change().fillna(0) * v).cumsum()
    out["pvt_green"] = (pvt > pvt.rolling(21, min_periods=5).mean()).astype(float)
    out["vol_base"] = v.shift(1).rolling(10, min_periods=5).mean()
    out["gap_pct"] = (out["ema9"] - out["ema24"]) / c
    return out


def entries(day: pd.DataFrame, ind: pd.DataFrame, rules: TraderRules) -> np.ndarray:
    """Signal bars (closes) satisfying the trader's entry conditions."""
    cross = (ind["ema9"] > ind["ema24"]) & (ind["ema9"].shift(1) <= ind["ema24"].shift(1))
    was_below = ind["gap_pct"].shift(1).rolling(rules.prior_lookback, min_periods=3).min() \
        <= -rules.prior_gap_pct
    spike = (day["volume"] / ind["vol_base"]).rolling(rules.spike_lookback, min_periods=1).max()
    ok = cross & was_below & (spike >= rules.vol_spike)
    ok &= ind["rsi"] < rules.rsi_max
    if rules.require_pvt_green:
        ok &= ind["pvt_green"] > 0
    if rules.require_macd_pos:
        ok &= ind["hist"] > 0
    m = day["minute_of_day"]
    ok &= (m >= rules.window[0]) & (m <= rules.window[1])
    return np.flatnonzero(ok.fillna(False).to_numpy())


@dataclass(frozen=True, slots=True)
class Fill:
    entry_i: int
    exit_i: int
    entry: float
    exit: float
    stop: float
    reason: str


def trade(day: pd.DataFrame, ind: pd.DataFrame, t: int, last_i: int) -> Fill | None:
    """Enter at t+1's open; exit on the trader's first-fade signals (fill next open),
    the structural stop (low of bars t-2..t; stop-first, gap-through at the open), or
    the last bar ``last_i`` (no overnight holds)."""
    n = len(day)
    if t + 1 > last_i:
        return None
    o, l, c, v = (day[k].to_numpy(float) for k in ("open", "low", "close", "volume"))
    hist, rsi, e9, e24 = (ind[k].to_numpy(float) for k in ("hist", "rsi", "ema9", "ema24"))
    entry = o[t + 1]
    stop = float(np.min(l[max(0, t - 2):t + 1]))
    if not entry > stop:
        return None
    rsi_was_hot = False
    for j in range(t + 1, n):
        if l[j] <= stop:
            px = o[j] if (j > t + 1 and o[j] < stop) else stop
            return Fill(t + 1, j, entry, px, stop, "stop")
        if j >= last_i:
            return Fill(t + 1, j, entry, c[j], stop, "close")
        rsi_was_hot |= rsi[j] >= 70
        reason = None
        if hist[j] > 0 and hist[j] < hist[j - 1]:
            reason = "macd_fade"                        # dark green -> light green
        elif rsi_was_hot and rsi[j] < 70:
            reason = "rsi_back_below_70"
        elif e9[j] < e24[j]:
            reason = "ema9_below_24"
        elif j >= t + 3 and c[j] > c[j - 2] and v[j] < v[j - 1] < v[j - 2]:
            reason = "volume_exhaustion"
        if reason and j + 1 < n:
            return Fill(t + 1, j + 1, entry, o[j + 1], stop, reason)
    return Fill(t + 1, n - 1, entry, c[n - 1], stop, "close")
