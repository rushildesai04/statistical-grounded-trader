"""Point-in-time features and 30-minute labels for the mega-cap test (prereg A1 applied).

Every session is laid on a fixed 390-minute grid (09:30 = offset 0). At decision offset k the
features read only bars 0..k-1 (all ended by the decision time); the label is open[k] -> open[k+30]
(or the session's last close). Cross-day inputs come from split-adjusted daily bars.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

ET = "America/New_York"
GRID = 390
DECISIONS = tuple(range(30, 361, 30))   # 10:00, 10:30, ..., 15:30 ET
HORIZON = 30
SLOT_LOOKBACK = 20
OWN_RET_N = (1, 5, 15, 30, 60, 120)
FEATURES = (
    [f"r{n}" for n in OWN_RET_N]
    + ["r_open", "gap", "prev_ret", "rv30", "rv120", "dvol_rel", "vwap_dist", "rsi14", "ema_spread",
       "dist_prev_high", "dist_prev_low",
       "spy_r5", "spy_r30", "spy_r_open", "qqq_r5", "qqq_r30", "qqq_r_open", "rel_spy_open",
       "minute", "dow"]
)


def to_grid(bars: pd.DataFrame) -> dict:
    """Regular-hours 1-minute frame (ts UTC, open/high/low/close/volume) -> per-session arrays
    on a 390-minute grid. Missing minutes carry the last close (open=high=low=close, volume 0)."""
    b = bars.copy()
    et = pd.to_datetime(b.ts, utc=True).dt.tz_convert(ET)
    b["session"] = et.dt.date
    b["k"] = (et.dt.hour * 60 + et.dt.minute) - (9 * 60 + 30)
    b = b[(b.k >= 0) & (b.k < GRID)]
    from sgt.intraday.calendar import default_calendar

    cal = default_calendar()
    out = {}
    for sess, g in b.groupby("session", sort=True):
        if not cal.is_session(sess):
            continue
        close_k = int((cal.bounds(sess).rth_close - pd.Timestamp(f"{sess} 09:30", tz=ET)).total_seconds() // 60)
        g = g[g.k < close_k]                       # early closes: drop post-market bars (A2)
        g = g.set_index("k").reindex(range(GRID))
        if g.close.notna().sum() < GRID // 2:
            continue  # half day or broken session
        c = g.close.ffill().bfill().to_numpy(float)
        o = g.open.fillna(pd.Series(c, index=g.index)).to_numpy(float)
        h = g.high.fillna(pd.Series(c, index=g.index)).to_numpy(float)
        lo = g.low.fillna(pd.Series(c, index=g.index)).to_numpy(float)
        v = g.volume.fillna(0.0).to_numpy(float)
        last = int(g.close.last_valid_index())     # last regular-session bar
        out[sess] = {"open": o, "high": h, "low": lo, "close": c, "volume": v, "last": last,
                     "close_k": close_k}
    return out


def _ema(x: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(x).ewm(span=span, adjust=False).mean().to_numpy()


def _rsi(c: np.ndarray, n: int = 14) -> np.ndarray:
    d = np.diff(c, prepend=c[0])
    up = pd.Series(np.clip(d, 0, None)).ewm(alpha=1 / n, adjust=False).mean()
    dn = pd.Series(np.clip(-d, 0, None)).ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + up / dn.replace(0, np.nan))).to_numpy()


def _ret(c: np.ndarray, k: int, n: int) -> float:
    return float(np.log(c[k - 1] / c[k - 1 - n])) if k - 1 - n >= 0 else np.nan


def session_rows(sess, a: dict, daily: pd.DataFrame, slot_dv: dict, spy: dict | None, qqq: dict | None) -> list[dict]:
    """Feature + label rows for one session. ``daily`` is indexed by date (adjusted OHLC);
    ``slot_dv`` maps decision k -> trailing mean dollar volume of that slot (prior sessions)."""
    o, c, h, lo, v = a["open"], a["close"], a["high"], a["low"], a["volume"]
    lr = np.diff(np.log(c), prepend=np.log(c[0]))
    typ = (h + lo + c) / 3
    cum_pv, cum_v = np.cumsum(typ * v), np.cumsum(v)
    rsi = _rsi(c)
    spread = (_ema(c, 9) - _ema(c, 21)) / c
    prev = daily.index[daily.index < sess]
    gap = prev_ret = d_hi = d_lo = np.nan
    if sess in daily.index and len(prev) >= 2:
        today, p1, p2 = daily.loc[sess], daily.loc[prev[-1]], daily.loc[prev[-2]]
        gap = float(np.log(today.open / p1.close))
        prev_ret = float(np.log(p1.close / p2.close))
        f = o[0] / today.open                      # adjusted -> today's raw units
        d_hi, d_lo = p1.high * f, p1.low * f
    rows = []
    last = a.get("last", GRID - 1)
    for k in DECISIONS:
        if k >= last:
            continue  # no real bars left to trade (2026-10-30: half-day sessions)
        px = c[k - 1]
        exit_px = o[k + HORIZON] if k + HORIZON <= last else c[last]
        dv = float((c[k - 30:k] * v[k - 30:k]).sum())
        base = slot_dv.get(k)
        r = {
            "session": sess, "k": k, "label": float(exit_px / o[k] - 1.0),
            **{f"r{n}": _ret(c, k, n) for n in OWN_RET_N},
            "r_open": float(np.log(px / o[0])), "gap": gap, "prev_ret": prev_ret,
            "rv30": float(lr[max(1, k - 30):k].std()), "rv120": float(lr[max(1, k - 120):k].std()),
            "dvol_rel": dv / base if base else np.nan,
            "vwap_dist": float(px / (cum_pv[k - 1] / cum_v[k - 1]) - 1) if cum_v[k - 1] > 0 else np.nan,
            "rsi14": float(rsi[k - 1]), "ema_spread": float(spread[k - 1]),
            "dist_prev_high": float(px / d_hi - 1) if d_hi == d_hi else np.nan,
            "dist_prev_low": float(px / d_lo - 1) if d_lo == d_lo else np.nan,
            "minute": k, "dow": pd.Timestamp(sess).dayofweek, "_dv": dv,
        }
        for name, m in (("spy", spy), ("qqq", qqq)):
            if m is None:
                r.update({f"{name}_r5": np.nan, f"{name}_r30": np.nan, f"{name}_r_open": np.nan})
            else:
                mc, mo = m["close"], m["open"]
                r.update({f"{name}_r5": _ret(mc, k, 5), f"{name}_r30": _ret(mc, k, 30),
                          f"{name}_r_open": float(np.log(mc[k - 1] / mo[0]))})
        r["rel_spy_open"] = r["r_open"] - r["spy_r_open"]
        rows.append(r)
    return rows


def build(bars: pd.DataFrame, daily: pd.DataFrame, spy_bars: pd.DataFrame | None = None,
          qqq_bars: pd.DataFrame | None = None) -> pd.DataFrame:
    """All rows for one ticker, sessions ascending. ``daily``: adjusted OHLC indexed by date."""
    grid = to_grid(bars)
    spy = to_grid(spy_bars) if spy_bars is not None else {}
    qqq = to_grid(qqq_bars) if qqq_bars is not None else {}
    hist: dict[int, list[float]] = {k: [] for k in DECISIONS}
    rows: list[dict] = []
    for sess in sorted(grid):
        slot = {k: float(np.mean(hist[k][-SLOT_LOOKBACK:])) if len(hist[k]) >= SLOT_LOOKBACK else None
                for k in DECISIONS}
        new = session_rows(sess, grid[sess], daily, slot, spy.get(sess), qqq.get(sess))
        for r in new:
            hist[r["k"]].append(r.pop("_dv"))
        rows.extend(new)
    return pd.DataFrame(rows)
