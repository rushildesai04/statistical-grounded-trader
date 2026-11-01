"""Entry/exit structure study on in-play days (2026-10-19 ground-up diagnosis).

Question: did fixed small take-profits + short time stops (every decision-model label so
far) destroy the convex payoff that momentum scalping depends on? Same entries, different
exits, same costs.

Entry (the trader's rules, point-in-time on 1-minute bars, regular session):
EMA9 crosses above EMA24 on bar t's close, bar-t volume >= 2x the mean of the prior 10
bars, close above session VWAP, RSI(14) < 70 (anti-chase), MACD histogram > 0.
Fill at bar t+1's open. Initial stop: the lowest low of bars t-2..t (structure).

Exits (all also exit at the cutoff before the close; the structural stop always applies):
* ``bracket``  -- +2 ATR target / -1 ATR stop / 30-minute time stop (what we tested);
* ``macd_fade``-- exit after 2 consecutive declines of a positive MACD histogram, or a
                  negative histogram (the trader's stated exit);
* ``ema9``     -- exit when a bar closes below EMA9 (momentum loss);
* ``chandelier`` -- trail 3 ATR below the highest high since entry.
Exit fills at the next bar's open (signal at close), stops at the stop or a gap-through
open. Costs from the gapper time-of-day model on both sides.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

EXITS = ("bracket", "macd_fade", "ema9", "chandelier")


def indicators(day: pd.DataFrame) -> pd.DataFrame:
    """1-minute indicators over one session's bars (time-ordered, all segments).
    Every value at row t uses rows <= t only."""
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
    tp = (h + l + c) / 3
    out["vwap"] = (tp * v).cumsum() / v.cumsum().replace(0, np.nan)
    out["vol_base"] = v.shift(1).rolling(10, min_periods=5).mean()
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    out["atr"] = tr.rolling(14, min_periods=5).mean()
    return out


def entry_signals(day: pd.DataFrame, ind: pd.DataFrame, rth: np.ndarray,
                  max_entry_minute: int) -> np.ndarray:
    cross = (ind["ema9"] > ind["ema24"]) & (ind["ema9"].shift(1) <= ind["ema24"].shift(1))
    ok = (cross & (day["volume"] >= 2.0 * ind["vol_base"]) & (day["close"] > ind["vwap"])
          & (ind["rsi"] < 70) & (ind["hist"] > 0) & pd.Series(rth, index=day.index)
          & (day["minute_of_day"] <= max_entry_minute))
    return np.flatnonzero(ok.fillna(False).to_numpy())


@dataclass(frozen=True, slots=True)
class Trade:
    exit_rule: str
    entry_i: int
    exit_i: int
    entry: float
    exit: float
    stop0: float
    gross: float
    net: float
    r_multiple: float      # net P&L per unit of initial risk


def simulate(day: pd.DataFrame, ind: pd.DataFrame, t: int, rule: str, cutoff_i: int,
             half: callable) -> Trade | None:
    """One long trade from signal bar ``t`` (entry at t+1 open) under exit ``rule``."""
    n = len(day)
    if t + 1 >= n or t + 1 > cutoff_i:
        return None
    o, h, l, c = (day[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    entry = o[t + 1]
    stop = float(np.min(l[max(0, t - 2):t + 1]))
    if not entry > stop:
        return None
    atr0 = float(ind["atr"].iloc[t]) if np.isfinite(ind["atr"].iloc[t]) else entry * 0.01
    tp = entry + 2.0 * atr0
    br_stop = max(stop, entry - 1.0 * atr0) if rule == "bracket" else stop
    hist, ema9, atr = (ind[k].to_numpy(float) for k in ("hist", "ema9", "atr"))
    hi = entry
    exit_px, exit_i = None, None
    for j in range(t + 1, n):
        cur_stop = br_stop
        if rule == "chandelier":
            cur_stop = max(stop, hi - 3.0 * (atr[j - 1] if np.isfinite(atr[j - 1]) else atr0))
        if l[j] <= cur_stop:                                     # stop first
            exit_px = o[j] if (j > t + 1 and o[j] < cur_stop) else cur_stop
            exit_i = j
            break
        if rule == "bracket":
            if h[j] >= tp:
                exit_px, exit_i = (o[j] if o[j] > tp and j > t + 1 else tp), j
                break
            if j - (t + 1) >= 30:
                exit_px, exit_i = c[j], j
                break
        hi = max(hi, h[j])
        if j >= cutoff_i:
            exit_px, exit_i = c[j], j
            break
        signal = False
        if rule == "macd_fade":
            signal = hist[j] < 0 or (hist[j] < hist[j - 1] < hist[j - 2] and hist[j - 2] > 0)
        elif rule == "ema9":
            signal = c[j] < ema9[j]
        if signal and j + 1 < n:
            exit_px, exit_i = o[j + 1], j + 1
            break
    if exit_px is None:
        exit_px, exit_i = c[n - 1], n - 1
    hin, hout = half(t + 1, entry), half(exit_i, exit_px)
    net = (exit_px * (1 - hout)) / (entry * (1 + hin)) - 1
    risk = (entry - stop) / entry
    return Trade(rule, t + 1, exit_i, entry, exit_px, stop, exit_px / entry - 1, net,
                 net / risk if risk > 0 else np.nan)
