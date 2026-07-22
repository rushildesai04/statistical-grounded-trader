"""Evaluation metrics. Accuracy alone is banned from reports — a directional
call near the base rate is noise. We report hit-rate vs base rate and PnL net
of costs.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def base_rate(y_true: pd.Series) -> float:
    """Accuracy of always predicting the most common class."""
    if len(y_true) == 0:
        return 0.0
    return float(y_true.value_counts(normalize=True).max())


def accuracy(y_true, y_pred) -> float:
    yt, yp = np.asarray(y_true), np.asarray(y_pred)
    return float((yt == yp).mean()) if len(yt) else 0.0


def traded_pnl(y_pred, fwd_return, cost: float) -> dict:
    """Trade only on non-flat predictions: long on 'up', short on 'down'.
    Charge `cost` (fractional round-trip) on every trade. Returns net PnL series
    stats. This is the number that matters."""
    yp = np.asarray(y_pred)
    r = np.asarray(fwd_return, dtype=float)
    pos = np.where(yp == "up", 1.0, np.where(yp == "down", -1.0, 0.0))
    traded = pos != 0
    pnl = pos * r - traded * cost
    n_trades = int(traded.sum())
    net = float(pnl.sum())
    wins = int(((pos * r > cost) & traded).sum())
    sharpe = 0.0
    active = pnl[traded]
    if active.size > 1 and active.std(ddof=0) > 0:
        sharpe = float(active.mean() / active.std(ddof=0) * np.sqrt(252))
    equity = np.cumsum(pnl)
    max_dd = float((np.maximum.accumulate(equity) - equity).max()) if equity.size else 0.0
    return {
        "n_trades": n_trades,
        "net_pnl": net,
        "avg_pnl_per_trade": float(net / n_trades) if n_trades else 0.0,
        "win_rate_on_trades": float(wins / n_trades) if n_trades else 0.0,
        "sharpe_annualized": sharpe,
        "max_drawdown": max_dd,
    }
