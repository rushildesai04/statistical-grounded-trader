"""Intrabar (live-candle) replay of the trader's entry (2026-10-19 diagnosis).

On 1-minute closes the trader's cross + volume signal is late: the move happens INSIDE the
minute the signal forms. A live chart updates the current candle tick by tick, so the
trader sees EMA9 pierce EMA24 and the volume bar balloon mid-minute. This module replays
that view from SIP trades: indicator state is carried from completed bars and advanced
PROVISIONALLY with each trade of the current minute (provisional close = last price,
provisional volume = cumulative size). The first trade at which every condition holds
is the trigger; the fill is the first trade at least ``latency_s`` later. Fake-outs (a
mid-minute cross that fails by the close) are traded like any other trigger.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

# Sale conditions that do not update the last-sale price on a live chart (odd lots,
# average-price, cash/next-day settlement, derivatively priced, prior reference, out of
# sequence, official open/close duplicates).
EXCLUDED_CONDITIONS = frozenset({"I", "W", "C", "N", "R", "4", "7", "Q", "O", "6", "M",
                                 "U", "Z", "P", "H", "V"})

_A9, _A24 = 2 / 10, 2 / 25
_A12, _A26, _A_SIG = 2 / 13, 2 / 27, 2 / 10


def clean_trades(rows: list[dict]) -> np.ndarray:
    """(t_ns, price, size) for price-setting trades, time-ordered."""
    out = []
    for r in rows:
        try:
            p, s = float(r["p"]), float(r["s"])
            t = pd.Timestamp(r["t"]).value
        except (KeyError, TypeError, ValueError):
            continue
        conds = set(r.get("c") or [])
        if p <= 0 or s <= 0 or conds & EXCLUDED_CONDITIONS:
            continue
        out.append((t, p, s))
    out.sort()
    return np.array(out, dtype=float).reshape(-1, 3)


@dataclass(frozen=True, slots=True)
class BarState:
    """Indicator state after the last COMPLETED bar (minute M-1)."""
    close: float
    ema9: float
    ema24: float
    ema12: float
    ema26: float
    signal: float
    rsi_up: float
    rsi_dn: float
    pvt: float
    pvt_hist20: tuple[float, ...]      # last 20 completed PVT values (for SMA21)
    vol_base: float                     # mean volume of the 10 bars before M
    recent_spike: float                 # max volume ratio over the last few completed bars
    was_below: bool                     # EMA9 sat >= prior_gap below EMA24 recently


def bar_state(day: pd.DataFrame, m: int, prior_gap_pct: float, lookback: int,
              spike_lookback: int) -> BarState | None:
    """State after completed bar m-1 of ``day`` (time-ordered 1-minute bars)."""
    if m < 30:
        return None
    c = day["close"].iloc[:m]
    v = day["volume"].iloc[:m]
    ema9 = c.ewm(span=9, adjust=False).mean()
    ema24 = c.ewm(span=24, adjust=False).mean()
    e12 = c.ewm(span=12, adjust=False).mean()
    e26 = c.ewm(span=26, adjust=False).mean()
    sig = (e12 - e26).ewm(span=9, adjust=False).mean()
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    pvt = (c.pct_change().fillna(0) * v).cumsum()
    base = v.rolling(10, min_periods=5).mean().shift(1)
    ratio = (v / base).iloc[-(spike_lookback - 1):] if spike_lookback > 1 else pd.Series([0.0])
    vb = float(v.iloc[-10:].mean())
    gap = ((ema9 - ema24) / c).iloc[-lookback:]
    return BarState(float(c.iloc[-1]), float(ema9.iloc[-1]), float(ema24.iloc[-1]),
                    float(e12.iloc[-1]), float(e26.iloc[-1]), float(sig.iloc[-1]),
                    float(up.iloc[-1]), float(dn.iloc[-1]), float(pvt.iloc[-1]),
                    tuple(float(x) for x in pvt.iloc[-20:]), vb,
                    float(np.nanmax(ratio.to_numpy())) if len(ratio) else 0.0,
                    bool((gap <= -prior_gap_pct).any()))


def first_trigger(st: BarState, ticks: np.ndarray, *, vol_spike: float, rsi_max: float,
                  require_macd_pos: bool, require_pvt_green: bool) -> int | None:
    """Index of the first tick in the minute at which the provisional candle satisfies the
    entry (cross from below + volume spike + filters), or None."""
    if not st.was_below or st.ema9 > st.ema24 or not st.vol_base > 0:
        return None
    vol = 0.0
    for k in range(len(ticks)):
        p, s = ticks[k, 1], ticks[k, 2]
        vol += s
        e9 = st.ema9 + _A9 * (p - st.ema9)
        e24 = st.ema24 + _A24 * (p - st.ema24)
        if e9 <= e24:
            continue
        if max(vol / st.vol_base, st.recent_spike) < vol_spike:
            continue
        dlt = p - st.close
        up = st.rsi_up + (max(dlt, 0.0) - st.rsi_up) / 14
        dn = st.rsi_dn + (max(-dlt, 0.0) - st.rsi_dn) / 14
        rsi = 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)
        if rsi >= rsi_max:
            continue
        if require_macd_pos:
            macd = (st.ema12 + _A12 * (p - st.ema12)) - (st.ema26 + _A26 * (p - st.ema26))
            if macd - (st.signal + _A_SIG * (macd - st.signal)) <= 0:
                continue
        if require_pvt_green:
            pvt = st.pvt + (p / st.close - 1.0) * vol
            if not pvt > (sum(st.pvt_hist20) + pvt) / (len(st.pvt_hist20) + 1):
                continue
        return k
    return None


def fill_after(ticks: np.ndarray, k: int, latency_s: float) -> int | None:
    """First tick at least ``latency_s`` after tick k (the bot's reaction + order time)."""
    t0 = ticks[k, 0] + latency_s * 1e9
    idx = np.flatnonzero(ticks[:, 0] >= t0)
    return int(idx[0]) if idx.size else None
