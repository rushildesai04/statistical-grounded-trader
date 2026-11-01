"""Daily-horizon features/labels (prereg_megacap_daily_2026-10-30).

Decision at 15:50 ET on day t: daily-bar features through t-1 plus today's 1-minute bars ending by
15:49 ET; label = adjusted close(t+1) / adjusted close(t) - 1 (market-on-close in and out).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.megacap.features import to_grid

CUTOFF_K = 379             # 15:49 ET bar (offset from 09:30)
HALF_DAY_LEAD = 10         # early close: last bar at least 10 minutes before the close
RET_N = (1, 2, 5, 10, 21, 63, 126, 252)
DAILY_FEATURES = (
    [f"d_r{n}" for n in RET_N]
    + ["d_rv21", "d_rv63", "d_ma20", "d_ma50", "d_ma200", "d_hi252", "d_lo252", "d_rsi14", "d_dvol",
       "t_gap", "t_r_intraday", "t_r_60", "t_range",
       "spy_t", "spy_r5", "spy_r21", "qqq_t", "qqq_r5", "qqq_r21", "rel_spy_t", "dow", "month"]
)


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def daily_block(d: pd.DataFrame) -> pd.DataFrame:
    """Per-date features known at the close of that date (shift by one before use for day t)."""
    c = d.close
    lr = np.log(c).diff()
    f = pd.DataFrame(index=d.index)
    for n in RET_N:
        f[f"d_r{n}"] = np.log(c / c.shift(n))
    f["d_rv21"], f["d_rv63"] = lr.rolling(21).std(), lr.rolling(63).std()
    for n in (20, 50, 200):
        f[f"d_ma{n}"] = c / c.rolling(n).mean() - 1
    f["d_hi252"] = c / d.high.rolling(252).max() - 1
    f["d_lo252"] = c / d.low.rolling(252).min() - 1
    f["d_rsi14"] = _rsi(c)
    if "volume" in d:
        dv = c * d.volume
        f["d_dvol"] = dv / dv.rolling(20).mean()
    else:
        f["d_dvol"] = np.nan
    return f


def _today(a: dict | None) -> tuple[float, float, float, float]:
    """(log return open -> cutoff, last-60-minute log return, range/open, cutoff index) or NaNs."""
    if a is None:
        return np.nan, np.nan, np.nan, np.nan
    k = min(CUTOFF_K, a["last"] - HALF_DAY_LEAD)
    o, c, h, lo = a["open"], a["close"], a["high"], a["low"]
    return (float(np.log(c[k] / o[0])), float(np.log(c[k] / c[max(0, k - 60)])),
            float((h[: k + 1].max() - lo[: k + 1].min()) / o[0]), float(k))


def build_daily(bars: pd.DataFrame, daily: pd.DataFrame, spy_bars: pd.DataFrame, spy_daily: pd.DataFrame,
                qqq_bars: pd.DataFrame, qqq_daily: pd.DataFrame, *, require_label: bool = True,
                live_session=None) -> pd.DataFrame:
    """Rows for every session with intraday bars. ``require_label=False`` (live decision) keeps the
    last session, whose next close does not exist yet (label NaN)."""
    grid, sg, qg = to_grid(bars), to_grid(spy_bars), to_grid(qqq_bars)
    if live_session is not None:
        # Session still open at 15:50: the cutoff comes from the exchange close, not the last bar seen.
        for g in (grid, sg, qg):
            if live_session in g:
                g[live_session] = dict(g[live_session], last=g[live_session]["close_k"] - 1)
    prior = daily_block(daily).shift(1)                       # through t-1 only
    sp, qp = daily_block(spy_daily).shift(1), daily_block(qqq_daily).shift(1)
    nxt_close = daily.close.shift(-1)
    rows = []
    for t in daily.index:
        if t not in grid or t not in prior.index or (require_label and pd.isna(nxt_close.get(t))):
            continue
        i = daily.index.get_loc(t)
        if i == 0:
            continue
        prev_close = daily.close.iloc[i - 1]
        gap = float(np.log(daily.open.loc[t] / prev_close))
        intr, r60, rng, _ = _today(grid[t])
        label = nxt_close.loc[t] / daily.close.loc[t] - 1.0 if pd.notna(nxt_close.loc[t]) else np.nan
        r = {"session": t, "label": float(label),
             **prior.loc[t].to_dict(), "t_gap": gap, "t_r_intraday": intr, "t_r_60": r60, "t_range": rng}
        for name, g, dd, blk in (("spy", sg, spy_daily, sp), ("qqq", qg, qqq_daily, qp)):
            if t in dd.index and t in g and dd.index.get_loc(t) > 0:
                j = dd.index.get_loc(t)
                m_gap = float(np.log(dd.open.iloc[j] / dd.close.iloc[j - 1]))
                r[f"{name}_t"] = m_gap + _today(g[t])[0]
            else:
                r[f"{name}_t"] = np.nan
            r[f"{name}_r5"] = blk.d_r5.get(t, np.nan)
            r[f"{name}_r21"] = blk.d_r21.get(t, np.nan)
        r["rel_spy_t"] = gap + intr - r["spy_t"]
        ts = pd.Timestamp(t)
        r["dow"], r["month"] = ts.dayofweek, ts.month
        rows.append(r)
    return pd.DataFrame(rows)
