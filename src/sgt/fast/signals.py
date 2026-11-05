"""Day-trader tape signals, point-in-time on 1-minute bars (owner request 2026-11-05).

``snapshot`` reads only bars that ENDED at or before ``at`` (bar start + 1 min <= at), so appending later bars can
never change a past reading (causal). Bars carry ``ts`` (UTC bar-start), open/high/low/close/volume and ``segment``
in {pre, rth, post} when present; segment is derived from the Eastern wall clock otherwise. EMA / MACD / RSI / PVT
match ``sgt.decision.trader_rules.indicators`` (Wilder RSI, EMA adjust=False, PVT vs its 21-bar SMA), with EMA 21
in place of EMA 24. VWAP, RVOL, off-HOD, cumulative volume and gap are regular-session (RTH from 09:30) measures.
Every value is a float or None; None means "not computable yet" (no RTH bar, too few bars, undefined indicator).
"""
from __future__ import annotations

import math
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
PSAR_STEP, PSAR_MAX = 0.02, 0.20


def _f(x) -> float | None:
    """A finite float, or None for NaN/inf/non-numeric (so the reading is always JSON-safe)."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _segment(b: pd.DataFrame) -> pd.Series:
    """Session segment per bar: the given column when present, else from the Eastern minute-of-day."""
    if "segment" in b.columns:
        return b["segment"].astype(str)
    mod = b.ts.dt.tz_convert(ET)
    mins = mod.dt.hour * 60 + mod.dt.minute
    return pd.Series(np.where(mins < 570, "pre", np.where(mins >= 960, "post", "rth")), index=b.index)


def _ended_before(bars: pd.DataFrame, at: pd.Timestamp) -> pd.DataFrame:
    """Bars of the session whose minute closed at or before ``at`` (bar start + 1 min <= at), time-ordered."""
    if bars.empty:
        return bars
    b = bars.assign(ts=pd.to_datetime(bars.ts, utc=True)).sort_values("ts")
    at = pd.Timestamp(at)
    at = at.tz_localize("UTC") if at.tzinfo is None else at.tz_convert("UTC")
    return b[b.ts + pd.Timedelta(minutes=1) <= at]


def _indicators(b: pd.DataFrame) -> dict[str, pd.Series]:
    """Trailing EMA 9/21, MACD 12/26/9, Wilder RSI 14, ATR 14 and PVT vs its 21-SMA (all causal, adjust=False)."""
    c, h, lo, v = (b[k].astype(float) for k in ("close", "high", "low", "volume"))
    ema9 = c.ewm(span=9, adjust=False).mean()
    ema21 = c.ewm(span=21, adjust=False).mean()
    macd = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    sig = macd.ewm(span=9, adjust=False).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    pc = c.shift(1)
    tr = pd.concat([h - lo, (h - pc).abs(), (lo - pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()
    pvt = (c.pct_change().fillna(0) * v).cumsum()
    return {"ema9": ema9, "ema21": ema21, "macd": macd, "signal": sig, "hist": macd - sig,
            "rsi": rsi, "atr": atr, "pvt": pvt, "pvt_sma": pvt.rolling(21, min_periods=5).mean()}


def _psar_side(h: np.ndarray, lo: np.ndarray) -> float | None:
    """Standard parabolic SAR (0.02 step, 0.20 cap): +1 when the last bar is in an up-trend, -1 in a down-trend."""
    n = len(h)
    if n < 2:
        return None
    bull = h[1] >= h[0]
    ep = max(h[0], h[1]) if bull else min(lo[0], lo[1])
    sar = lo[0] if bull else h[0]
    af = PSAR_STEP
    for i in range(1, n):
        sar = sar + af * (ep - sar)
        if bull:
            sar = min(sar, lo[i - 1], lo[i - 2] if i >= 2 else lo[i - 1])
            if lo[i] < sar:
                bull, sar, ep, af = False, ep, lo[i], PSAR_STEP
            elif h[i] > ep:
                ep, af = h[i], min(af + PSAR_STEP, PSAR_MAX)
        else:
            sar = max(sar, h[i - 1], h[i - 2] if i >= 2 else h[i - 1])
            if h[i] > sar:
                bull, sar, ep, af = True, ep, h[i], PSAR_STEP
            elif lo[i] < ep:
                ep, af = lo[i], min(af + PSAR_STEP, PSAR_MAX)
    return 1.0 if bull else -1.0


def snapshot(bars: pd.DataFrame, at: pd.Timestamp, prev_close: float | None = None,
             prior_day_volume: float | None = None) -> dict:
    """Day-trader signals as of ``at``, from one session's bars that closed at or before it. All floats or None."""
    keys = ("ema9_21_gap_pct", "ema_cross_state", "minutes_since_cross", "vwap_dist_pct", "rsi14", "macd_hist_pct",
            "rvol_5m", "cum_vol_vs_prev_day", "atr14_1m_pct", "off_hod_pct", "above_premarket_high", "pvt_trend",
            "psar_side", "gap_pct", "minutes_since_open")
    out: dict[str, float | None] = dict.fromkeys(keys)
    b = _ended_before(bars, at)
    if b.empty:
        return out
    seg = _segment(b)
    ind = _indicators(b)
    c = b.close.astype(float)
    close = _f(c.iloc[-1])

    e9, e21 = ind["ema9"], ind["ema21"]
    if close:
        out["ema9_21_gap_pct"] = _f((e9.iloc[-1] - e21.iloc[-1]) / close)
        out["macd_hist_pct"] = _f(ind["hist"].iloc[-1] / close)
        out["atr14_1m_pct"] = _f(ind["atr"].iloc[-1] / close)
    out["rsi14"] = _f(ind["rsi"].iloc[-1])

    side = np.sign((e9 - e21).to_numpy())
    cur = side[-1]
    out["ema_cross_state"] = _f(cur) if cur != 0 else None
    flips = np.flatnonzero((side[1:] != side[:-1]) & (side[1:] != 0) & (side[:-1] != 0))
    if len(flips):
        cross_ts = b.ts.iloc[int(flips[-1]) + 1]
        out["minutes_since_cross"] = _f((b.ts.iloc[-1] - cross_ts).total_seconds() / 60)

    sma = ind["pvt_sma"].iloc[-1]
    if pd.notna(sma):
        out["pvt_trend"] = 1.0 if ind["pvt"].iloc[-1] > sma else -1.0

    out["psar_side"] = _psar_side(b.high.to_numpy(float), b.low.to_numpy(float))

    rth = b[seg.to_numpy() == "rth"]
    if len(rth):
        tp = (rth.high + rth.low + rth.close) / 3 * rth.volume.astype(float)
        cum = rth.volume.astype(float).cumsum()
        vwap = (tp.cumsum() / cum.where(cum > 0)).iloc[-1]
        if _f(vwap):
            out["vwap_dist_pct"] = _f((rth.close.iloc[-1] - vwap) / vwap)
        hod = rth.high.astype(float).max()
        if hod:
            out["off_hod_pct"] = _f((rth.close.iloc[-1] - hod) / hod)
        vol = rth.volume.astype(float)
        base = vol.mean()
        if base:
            out["rvol_5m"] = _f(vol.tail(5).mean() / base)
        pv = _f(prior_day_volume)
        if pv:
            out["cum_vol_vs_prev_day"] = _f(vol.sum() / pv)
        pc = _f(prev_close)
        if pc:
            out["gap_pct"] = _f((rth.open.iloc[0] - pc) / pc)
        out["minutes_since_open"] = _f((b.ts.iloc[-1] + pd.Timedelta(minutes=1) - rth.ts.iloc[0]).total_seconds() / 60)

    pre = b[seg.to_numpy() == "pre"]
    if len(pre) and close is not None:
        pmh = pre.high.astype(float).max()
        if _f(pmh) is not None:
            out["above_premarket_high"] = 1.0 if close > pmh else 0.0
    return out


def series(bars: pd.DataFrame) -> dict[str, list]:
    """Per-bar RSI 14 and MACD (line / signal / histogram) for charting, causal ewm only; None where undefined."""
    b = bars.assign(ts=pd.to_datetime(bars.ts, utc=True)).sort_values("ts")
    if b.empty:
        return {"rsi": [], "macd": [], "signal": [], "hist": []}
    ind = _indicators(b)
    return {"rsi": [_f(x) for x in ind["rsi"]], "macd": [_f(x) for x in ind["macd"]],
            "signal": [_f(x) for x in ind["signal"]], "hist": [_f(x) for x in ind["hist"]]}
