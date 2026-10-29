"""Daily backtest for monthly-rebalanced long-only allocations (pre-registered mechanics).

Signal at month-end close ``t`` (data <= t), execution at the NEXT trading day's close. Between
rebalances weights drift with prices. Costs: ``cost_bp`` per unit of turnover, charged on the
execution day. Unallocated weight is held in the cash asset.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sgt.allocation.signals import Strategy, month_ends


@dataclass
class BacktestResult:
    returns: pd.Series          # daily portfolio returns (net of costs)
    weights: pd.DataFrame       # post-trade target weights at each execution day
    turnover: pd.Series         # one-sided turnover at each execution day


def run(prices: pd.DataFrame, cash: str, strategy: Strategy, *, start: pd.Timestamp,
        cost_bp: float = 5.0) -> BacktestResult:
    prices = prices.dropna(how="any")
    rets = prices.pct_change().fillna(0.0)
    dates = prices.index
    signals = [d for d in month_ends(dates) if d >= start]
    exec_on = {}
    for s in signals:
        nxt = dates[dates > s]
        if len(nxt):
            exec_on[nxt[0]] = s
    cols = list(prices.columns)
    w = pd.Series(0.0, index=cols)
    w[cash] = 1.0
    out, wrec, trec = [], {}, {}
    started = False
    for d in dates:
        if d < start:
            continue
        r = float((w * rets.loc[d]).sum()) if started else 0.0
        # drift weights through today's returns
        if started:
            grown = w * (1.0 + rets.loc[d])
            tot = grown.sum()
            w = grown / tot if tot > 0 else w
        if d in exec_on:
            tgt = strategy(prices, cash, exec_on[d]).reindex(cols).fillna(0.0)
            tgt = tgt.clip(lower=0.0)
            gross = tgt.drop(cash, errors="ignore").sum()
            if gross > 1.0:
                tgt = tgt / gross
            tgt[cash] = max(0.0, 1.0 - tgt.drop(cash, errors="ignore").sum())
            to = 0.5 * float((tgt - w).abs().sum())
            r -= 2.0 * to * cost_bp / 1e4            # buys + sells each pay cost_bp
            w = tgt
            wrec[d], trec[d] = tgt, to
            started = True
        out.append((d, r))
    s = pd.Series(dict(out))
    return BacktestResult(s, pd.DataFrame(wrec).T, pd.Series(trec))
