"""Money score for the daily model's calls (docs/findings/prereg_money_score_2026-11-14.md).

Expected net return of a call = side x calibration x forecast - cost; the size multiplier compares that expected money
per unit of today's risk with the typical call so far. Every input is known at the 15:50 ET decision: the calibration
and the reference of month M use only sessions before the first day of M.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COST = 0.0004
COST_BY_SYMBOL = {"RDDT": 0.0010}
M_LO, M_HI = 0.25, 2.0
BASE_RISK, CAP, GROSS = 0.01, 0.25, 1.0


def cost(symbol: str) -> float:
    return COST_BY_SYMBOL.get(symbol, COST)


def _month(session: pd.Series) -> pd.Series:
    return session.astype(str).str[:7]


def calibration(rows: pd.DataFrame) -> dict[str, float]:
    """{month: b}: the slope of ``label`` on ``pred`` over all rows of earlier months, clipped to [0, 1].
    A month with fewer than 2 earlier rows, or a flat forecast, gets 0 (no information: no trade)."""
    r = rows[["session", "pred", "label"]].dropna()
    month = _month(r.session)
    out: dict[str, float] = {}
    for m in sorted(month.unique()):
        past = r[month < m]
        x, y = past.pred.to_numpy(float), past.label.to_numpy(float)
        if len(x) < 2 or np.var(x) <= 0:
            out[m] = 0.0
            continue
        out[m] = float(np.clip(np.cov(x, y, ddof=0)[0, 1] / np.var(x), 0.0, 1.0))
    return out


def expected(rows: pd.DataFrame, b: dict[str, float]) -> pd.DataFrame:
    """Rows with ``ev`` (expected net return of the row's call; NaN when flat) and ``z`` = ev / d_rv21."""
    t = rows.copy()
    t["month"] = _month(t.session)
    c = t.symbol.map(cost).astype(float)
    slope = t.month.map(b).fillna(0.0)
    t["ev"] = np.where(t.position != 0, t.position * slope * t.pred - c, np.nan)
    t["z"] = t.ev / t.d_rv21.where(t.d_rv21 > 0)
    return t


def reference(t: pd.DataFrame) -> dict[str, float]:
    """{month: z_ref}: the median ``z`` of calls with ev > 0 in earlier months (NaN until there is one)."""
    calls = t[(t.position != 0) & (t.ev > 0) & t.z.notna()]
    return {m: float(calls.z[calls.month < m].median()) if (calls.month < m).any() else float("nan")
            for m in sorted(t.month.unique())}


def multiplier(t: pd.DataFrame, ref: dict[str, float]) -> pd.Series:
    """0 when ev <= 0 or unknown (no trade); else z / z_ref clipped to [0.25, 2]. No reference yet -> 1."""
    zr = t.month.map(ref)
    m = (t.z / zr).clip(M_LO, M_HI).where(zr.notna() & (zr > 0), 1.0)
    return m.where((t.ev > 0) & t.z.notna(), 0.0).where(t.position != 0, 0.0)


def base_weight(sigma: pd.Series) -> pd.Series:
    return (BASE_RISK / sigma.where(sigma > 0)).clip(upper=CAP).fillna(0.0)


def book(t: pd.DataFrame, weight: pd.Series) -> pd.DataFrame:
    """Per session: gross and net return of a book holding ``weight`` (>= 0, per call) in each call's direction,
    all weights of a day scaled down together when they sum above the gross cap."""
    w = weight.where(t.position != 0, 0.0).clip(upper=CAP)
    gross = w.groupby(t.session).transform("sum")
    w = w * (GROSS / gross).clip(upper=1.0).fillna(1.0)
    c = t.symbol.map(cost).astype(float)
    ret = w * (t.position * t.label - c)
    return pd.DataFrame({"gross": w.groupby(t.session).sum(), "ret": ret.groupby(t.session).sum()})


# --- Record score (docs/findings/prereg_record_score_2026-11-14.md) --------------------------------------------------
K = 100.0                       # an earlier record of K calls weighs as much as the side's pooled record


def record_ev(calls: pd.DataFrame, k: float = K) -> pd.Series:
    """Expected net return of each call from the record so far: the mean net of earlier calls of the same stock and
    side, shrunk toward the mean of all earlier calls of that side. "Earlier" = sessions before the first day of
    the call's calendar month. ``calls``: columns session, symbol, position (+1 / -1), net. No earlier call of the
    side at all -> 0."""
    c = calls[["symbol", "position", "net"]].copy()
    c["month"] = _month(calls.session)
    own = c.groupby(["symbol", "position", "month"], sort=True).net.agg(s="sum", n="count").reset_index()
    grp = own.groupby(["symbol", "position"])
    own["cs"], own["cn"] = grp.s.cumsum() - own.s, grp.n.cumsum() - own.n          # earlier months only
    side = c.groupby(["position", "month"], sort=True).net.agg(s="sum", n="count").reset_index()
    g2 = side.groupby("position")
    side["ps"], side["pn"] = g2.s.cumsum() - side.s, g2.n.cumsum() - side.n
    m = c.merge(own[["symbol", "position", "month", "cs", "cn"]], on=["symbol", "position", "month"], how="left") \
         .merge(side[["position", "month", "ps", "pn"]], on=["position", "month"], how="left")
    pooled = (m.ps / m.pn.where(m.pn > 0))
    ev = ((m.cs + k * pooled) / (m.cn + k)).where(m.pn > 0, 0.0)
    return pd.Series(ev.to_numpy(float), index=calls.index, name="ev")
