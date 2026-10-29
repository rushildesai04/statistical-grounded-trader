"""Portfolio metrics for the pre-registered allocation test."""
from __future__ import annotations

import numpy as np
import pandas as pd


def summarize(r: pd.Series, cash_r: pd.Series, turnover: pd.Series | None = None) -> dict:
    r = r.dropna()
    if len(r) < 20:
        return {}
    eq = (1 + r).cumprod()
    years = len(r) / 252.0
    ex = r - cash_r.reindex(r.index).fillna(0.0)
    dd = eq / eq.cummax() - 1.0
    yearly = (1 + r).groupby(r.index.year).prod() - 1.0
    out = {
        "CAGR": eq.iloc[-1] ** (1 / years) - 1.0,
        "vol": r.std(ddof=1) * np.sqrt(252),
        "sharpe": ex.mean() / ex.std(ddof=1) * np.sqrt(252) if ex.std(ddof=1) > 0 else np.nan,
        "max_dd": dd.min(),
        "worst_year": yearly.min(),
        "total": eq.iloc[-1] - 1.0,
    }
    if turnover is not None and len(turnover):
        out["turnover_yr"] = turnover.sum() / years
    return out
