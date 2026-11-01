"""Monthly allocation signals (pre-registered: docs/findings/prereg_etf_allocation_2026-10-29.md).

Every function receives the full price frame and a signal date ``t`` and reads ONLY rows with
index <= t. Weights are long-only and sum to <= 1; the remainder is held in cash.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd

LOOKBACK = 252          # 12 months of trading days
VOL_WINDOW = 63         # 3 months
TARGET_VOL = 0.10
Strategy = Callable[[pd.DataFrame, str, pd.Timestamp], pd.Series]


def month_ends(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Last trading day of each calendar month present in ``index``."""
    s = pd.Series(index, index=index)
    return pd.DatetimeIndex(s.groupby([index.year, index.month]).max().to_numpy())


def _hist(prices: pd.DataFrame, t: pd.Timestamp) -> pd.DataFrame:
    return prices.loc[:t]


def _total_return(h: pd.DataFrame, n: int) -> pd.Series:
    if len(h) <= n:
        return pd.Series(np.nan, index=h.columns)
    return h.iloc[-1] / h.iloc[-1 - n] - 1.0


def tsmom_vol_scaled(prices: pd.DataFrame, cash: str, t: pd.Timestamp) -> pd.Series:
    """S1: hold asset iff its 12-month return beats cash; w = (1/N) * min(1, 10% / vol)."""
    h = _hist(prices, t)
    risk = [c for c in prices.columns if c != cash]
    ret = _total_return(h, LOOKBACK)
    vol = h[risk].pct_change().iloc[-VOL_WINDOW:].std(ddof=1) * np.sqrt(252)
    on = (ret[risk] > ret[cash]) & vol.notna() & (vol > 0)
    w = (1.0 / len(risk)) * np.minimum(1.0, TARGET_VOL / vol)
    return w.where(on, 0.0).fillna(0.0)


def gtaa_trend(prices: pd.DataFrame, cash: str, t: pd.Timestamp) -> pd.Series:
    """S2: 1/N if the month-end close is above its 10-month SMA of month-end closes."""
    h = _hist(prices, t)
    risk = [c for c in prices.columns if c != cash]
    me = h.loc[month_ends(h.index)]
    if len(me) < 10:
        return pd.Series(0.0, index=risk)
    sma = me[risk].iloc[-10:].mean()
    return ((h[risk].iloc[-1] > sma).astype(float) / len(risk))


def dual_momentum_top3(prices: pd.DataFrame, cash: str, t: pd.Timestamp) -> pd.Series:
    """S3: top 3 by 12-month return, 1/3 each, only those beating cash."""
    h = _hist(prices, t)
    risk = [c for c in prices.columns if c != cash]
    ret = _total_return(h, LOOKBACK)
    top = ret[risk].dropna().sort_values(ascending=False).head(3)
    keep = top[top > ret[cash]].index
    return pd.Series({c: (1 / 3 if c in keep else 0.0) for c in risk})


def fixed(weights: dict[str, float]) -> Strategy:
    """Static benchmark weights, rebalanced at every signal date."""
    def f(prices: pd.DataFrame, cash: str, t: pd.Timestamp) -> pd.Series:
        return pd.Series({c: float(weights.get(c, 0.0)) for c in prices.columns if c != cash})
    return f


def sixty_forty_trend(prices: pd.DataFrame, cash: str, t: pd.Timestamp) -> pd.Series:
    """F2 (forward prereg 2026-10-29): 60% SPY iff SPY's 12-month return beats cash, 40% IEF
    iff IEF's does; a failing leg's weight goes to cash."""
    ret = _total_return(_hist(prices, t), LOOKBACK)
    legs = {"SPY": 0.6, "IEF": 0.4}
    return pd.Series({c: (legs.get(c, 0.0) if c in legs and ret[c] > ret[cash] else 0.0)
                      for c in prices.columns if c != cash})
