"""C2 alt pump fade (prereg_crypto_pumpfade_2026-11-05): run-up events and short-trade returns. Pure functions.

Frames: ``ts`` (UTC minute start), ``open``, ``high``, ``low``, ``close`` (Coinbase candles; a missing minute had
no trades). The development gate is C1's (``sgt.crypto.leadlag.gate``).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RUN_MIN, Q, MIN_RUN, MIN_EXCESS = 30, 0.999, 0.03, 0.02
WINDOW_MIN, GAP_MIN, HOLD_MIN, STOP, COST = 30 * 1440, 6 * 60, 60, 0.10, 0.0010
SEED = 20261105


def _close_grid(f: pd.DataFrame) -> pd.Series:
    """Close on a full minute grid, carried forward from the last candle (a missing minute had no trades)."""
    return f.set_index("ts").close.sort_index().resample("1min").last().ffill()


def run_up(f: pd.DataFrame, minutes: int = RUN_MIN) -> pd.Series:
    """r30 at each candle: close / close of the last candle at or before ``minutes`` earlier - 1."""
    g = _close_grid(f)
    c = f.set_index("ts").close.sort_index()
    prev = g.shift(minutes).reindex(c.index)
    return (c / prev - 1).dropna()


def events(f: pd.DataFrame, btc: pd.DataFrame, q: float = Q, min_run: float = MIN_RUN,
           min_excess: float | None = MIN_EXCESS, window: int = WINDOW_MIN, gap: int = GAP_MIN) -> pd.DataFrame:
    """Coin minutes with an outsized run-up (point-in-time own threshold, absolute floor, excess over BTC).
    Events within ``gap`` minutes after a kept event are dropped. Returns ts, run, btc_run, thr."""
    r = run_up(f)
    if r.empty:
        return pd.DataFrame(columns=["ts", "run", "btc_run", "thr"])
    thr = r.rolling(f"{window}min", min_periods=1).quantile(q).shift(1)
    full = (r.index - r.index[0]) >= pd.Timedelta(minutes=window)
    bg = _close_grid(btc)
    b = (bg / bg.shift(RUN_MIN) - 1).reindex(r.index)
    hit = (r >= np.maximum(thr, min_run)) & thr.notna() & full
    if min_excess is not None:
        hit &= (r - b) >= min_excess
    kept, last = [], None
    for t in r.index[hit.to_numpy()]:
        if last is None or t - last > pd.Timedelta(minutes=gap):
            kept.append(t)
            last = t
    return pd.DataFrame({"ts": kept, "run": r.loc[kept].to_numpy(), "btc_run": b.loc[kept].to_numpy(),
                         "thr": thr.loc[kept].to_numpy()})


def trade(f: pd.DataFrame, ev: pd.DataFrame, hold: int = HOLD_MIN, stop: float | None = STOP,
          cost: float = COST, side: int = -1) -> pd.DataFrame:
    """Enter at the open of bar t+1 (side -1 short, C2; +1 long, C2M prereg_crypto_momentum); stop at entry x
    (1 -/+ stop) on a bar high/low (gap: that bar's open); else the close of the last candle in t+1 .. t+hold.
    Events without a candle at t+1 are skipped."""
    if side not in (-1, 1):
        raise ValueError("side must be -1 or +1")
    x = f.set_index("ts").sort_index()
    rows = []
    for e in ev.itertuples(index=False):
        t1 = e.ts + pd.Timedelta(minutes=1)
        if t1 not in x.index:
            continue
        w = x.loc[t1:t1 + pd.Timedelta(minutes=hold - 1)]
        entry = float(w.open.iloc[0])
        cover, stopped = float(w.close.iloc[-1]), False
        if stop is not None:
            lvl = entry * (1 - side * stop)
            hits = w[w.high >= lvl] if side < 0 else w[w.low <= lvl]
            if len(hits):
                gap = float(hits.open.iloc[0])
                cover, stopped = (max(lvl, gap) if side < 0 else min(lvl, gap)), True
        g = side * (cover - entry) / entry
        rows.append({"ts": e.ts, "run": e.run, "entry": entry, "cover": cover, "stopped": stopped,
                     "gross": g, "net": g - cost})
    return pd.DataFrame(rows, columns=["ts", "run", "entry", "cover", "stopped", "gross", "net"])
