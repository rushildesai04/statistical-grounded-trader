"""Daily (swing) features and triggers, point-in-time at the signal day's CLOSE (A, 2026-10-18).

A row at day ``t`` may read bars ``<= t`` only; the trade (labels.py) enters at ``t+1``'s open.
Every rolling window below is trailing and includes ``t``; nothing is centered or forward.

Triggers (any may fire; the first in TRIGGER_ORDER names the row):
* ``reversal_5d``  -- 5-day return <= -10% and close in the lower half of the 20d range:
  short-term overreaction (liquidity-provision reversal);
* ``gap_volume``   -- today's open gapped >= +5% over yesterday's close on >= 2x volume
  and closed above the open: news continuation;
* ``high_52w``     -- close at a new 252-day closing high on >= 1.5x volume: momentum;
* ``pullback_trend`` -- close above the rising 50d average but down >= 5% over 5 days
  and within 1 ATR of the 20d average: buy-the-dip in an uptrend.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRIGGER_ORDER = ("gap_volume", "high_52w", "reversal_5d", "pullback_trend")

FEATURES = (
    "f_ret_1", "f_ret_5", "f_ret_20", "f_ret_60", "f_mom_12_1", "f_vol_20", "f_atr_pct",
    "f_dist_52w_high", "f_dist_52w_low", "f_rel_vol", "f_log_dollar_vol", "f_log_price",
    "f_gap", "f_range_pct", "f_close_loc", "f_dist_ma20_atr", "f_dist_ma50_atr", "f_ma50_slope",
    "f_dow", "f_mkt_ret_5", "f_mkt_ret_20", "f_mkt_above_200", "f_mkt_vol_20",
)


def _atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()],
                   axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def symbol_features(df: pd.DataFrame) -> pd.DataFrame:
    """Per-symbol trailing features. ``df``: date-sorted daily bars (date, OHLCV)."""
    c, o, h, l, v = df["close"], df["open"], df["high"], df["low"], df["volume"]
    r = c.pct_change()
    atr = _atr(df)
    ma20, ma50 = c.rolling(20).mean(), c.rolling(50).mean()
    out = pd.DataFrame(index=df.index)
    out["f_ret_1"] = r
    out["f_ret_5"] = c / c.shift(5) - 1
    out["f_ret_20"] = c / c.shift(20) - 1
    out["f_ret_60"] = c / c.shift(60) - 1
    out["f_mom_12_1"] = c.shift(21) / c.shift(252) - 1
    out["f_vol_20"] = r.rolling(20).std()
    out["f_atr_pct"] = atr / c
    out["f_dist_52w_high"] = c / c.rolling(252, min_periods=120).max() - 1
    out["f_dist_52w_low"] = c / c.rolling(252, min_periods=120).min() - 1
    vol_base = v.shift(1).rolling(20, min_periods=10).mean()
    out["f_rel_vol"] = np.log((v / vol_base).replace(0, np.nan))
    out["f_log_dollar_vol"] = np.log((c * v).rolling(20, min_periods=10).mean().replace(0, np.nan))
    out["f_log_price"] = np.log(c)
    out["f_gap"] = o / c.shift(1) - 1
    out["f_range_pct"] = (h - l) / c
    rng = (h - l).replace(0, np.nan)
    out["f_close_loc"] = (c - l) / rng
    out["f_dist_ma20_atr"] = (c - ma20) / atr
    out["f_dist_ma50_atr"] = (c - ma50) / atr
    out["f_ma50_slope"] = ma50 / ma50.shift(10) - 1
    out["f_dow"] = pd.to_datetime(df["date"]).dt.dayofweek.astype(float)
    out["_atr"] = atr
    out["_dollar_vol_20"] = (c * v).rolling(20, min_periods=10).mean()
    # triggers
    lo20, hi20 = l.rolling(20).min(), h.rolling(20).max()
    rv = v / vol_base
    trig = pd.Series(pd.NA, index=df.index, dtype="object")
    conds = {
        "gap_volume": (out["f_gap"] >= 0.05) & (rv >= 2.0) & (c > o),
        "high_52w": (c >= c.rolling(252, min_periods=252).max()) & (rv >= 1.5),
        "reversal_5d": (out["f_ret_5"] <= -0.10) & ((c - lo20) / (hi20 - lo20) < 0.5),
        "pullback_trend": (c > ma50) & (out["f_ma50_slope"] > 0) & (out["f_ret_5"] <= -0.05)
                          & ((c - ma20).abs() <= atr),
    }
    for name in reversed(TRIGGER_ORDER):           # first in TRIGGER_ORDER wins
        trig = trig.mask(conds[name].fillna(False), name)
    out["trigger"] = trig
    num = out.columns.difference(["trigger"])
    out[num] = out[num].replace([np.inf, -np.inf], np.nan)   # zero ATR / zero volume base
    return out


def market_features(spy: pd.DataFrame) -> pd.DataFrame:
    """Market context from SPY daily bars, keyed by date."""
    c = spy["close"]
    r = c.pct_change()
    ma200 = c.rolling(200).mean()
    return pd.DataFrame({
        "date": spy["date"].values,
        "f_mkt_ret_5": (c / c.shift(5) - 1).values,
        "f_mkt_ret_20": (c / c.shift(20) - 1).values,
        # NaN until 200 days exist: a NaN average must not read as "below" (it did,
        # labelling the first 200 sessions a bear market; fixed 2026-10-18).
        "f_mkt_above_200": (c > ma200).astype(float).where(ma200.notna()).values,
        "f_mkt_vol_20": r.rolling(20).std().values,
    })
