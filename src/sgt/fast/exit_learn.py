"""Learned fade exit (prereg_fade_exit_learn_2026-11-06): checkpoint states of an open short and the policy.

At 10/20/30/40/50 minutes after entry, for a trade the champion (60 min, 20% stop) still holds, describe the trade
from bars that ENDED by the checkpoint and label what holding to the champion's exit earns over covering at the next
bar's open. Pure functions; ``rth`` is one session's RTH 1-minute bars (ts UTC bar start), sorted.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.fast import signals
from sgt.fast.exits import CHAMPION, indicators, simulate

CHECKPOINTS = (10, 20, 30, 40, 50)
SIGS = ("ema9_21_gap_pct", "ema_cross_state", "vwap_dist_pct", "rsi14", "macd_hist_pct", "rvol_5m", "atr14_1m_pct",
        "off_hod_pct", "pvt_trend", "psar_side")
FEATURES = ["x_gain", "x_best", "x_giveback", "x_min", "x_minute_day", "x_logpx", "x_type"] + [f"s_{k}" for k in SIGS]
TYPES = ("volume_increasing", "volume_spike", "gaining_volume", "gaining_momentum", "new_high", "intraday_high",
         "testing_high", "price_level", "resumed", "news")
MARGIN = 0.005                                    # secondary policy: cover only if holding is predicted < -0.5%


def checkpoints(rth: pd.DataFrame, entry_ts: pd.Timestamp, entry: float, alert_type: str) -> tuple[dict | None, list]:
    """The champion's result and one row per checkpoint the trade is still open at (features, cover-now price,
    label = (cover-now - champion exit) / entry)."""
    if rth.empty:
        return None, []
    ind = indicators(rth.reset_index(drop=True))
    champ = simulate(ind, entry_ts, entry, CHAMPION)
    if champ is None:
        return None, []
    ts = ind.ts
    i0 = int(np.flatnonzero((ts >= entry_ts).to_numpy())[0])
    exit_i = i0 + champ["minutes"] - 1
    champ_px = entry * (1 - champ["gross"])
    o, lo, c = (ind[k].to_numpy(float) for k in ("open", "low", "close"))
    et = ts.dt.tz_convert("America/New_York")
    rows = []
    for k in CHECKPOINTS:
        cut = entry_ts + pd.Timedelta(minutes=k)
        ended = np.flatnonzero(((ts + pd.Timedelta(minutes=1)) <= cut).to_numpy())
        if not len(ended):
            continue
        ik = int(ended[-1])
        if ik < i0 or ik >= exit_i:              # not entered yet, or the champion is already out by this bar
            break
        now_px = float(o[ik + 1])
        gain = entry / c[ik] - 1
        best = entry / lo[i0:ik + 1].min() - 1
        snap = signals.snapshot(rth.iloc[:ik + 1], cut)
        row = {"k": k, "now_px": now_px, "label": (now_px - champ_px) / entry,
               "x_gain": gain, "x_best": best, "x_giveback": best - gain, "x_min": float(k),
               "x_minute_day": float(et.iloc[ik].hour * 60 + et.iloc[ik].minute),
               "x_logpx": float(np.log(c[ik])), "x_type": float(TYPES.index(alert_type)) if alert_type in TYPES else np.nan}
        row.update({f"s_{s}": (np.nan if snap.get(s) is None else float(snap[s])) for s in SIGS})
        rows.append(row)
    return champ, rows


def policy_gross(entry: float, champ_gross: float, rows: pd.DataFrame, pred: np.ndarray, margin: float = 0.0) -> tuple[float, int | None]:
    """Gross of the learned exit for one event: cover at the first checkpoint whose predicted label < -margin."""
    for r, p in zip(rows.itertuples(index=False), pred):
        if p < -margin:
            return (entry - r.now_px) / entry, int(r.k)
    return champ_gross, None
