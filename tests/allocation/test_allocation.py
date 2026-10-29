"""Allocation: no look-ahead, next-day execution, drift, costs, signal rules."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.allocation.backtest import run
from sgt.allocation.signals import fixed, gtaa_trend, month_ends, tsmom_vol_scaled


def _prices(n=600, seed=0):
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(seed)
    up = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.01, n)))
    down = 100 * np.exp(np.cumsum(rng.normal(-0.001, 0.01, n)))
    cash = 100 * np.exp(np.cumsum(np.full(n, 0.0001)))
    return pd.DataFrame({"UP": up, "DOWN": down, "CASH": cash}, index=idx)


def test_signals_ignore_future_rows():
    p = _prices()
    t = p.index[400]
    base = tsmom_vol_scaled(p, "CASH", t)
    fut = p.copy()
    fut.iloc[401:] *= 5.0                              # rewrite the future
    pd.testing.assert_series_equal(base, tsmom_vol_scaled(fut, "CASH", t))
    pd.testing.assert_series_equal(gtaa_trend(p, "CASH", t), gtaa_trend(fut, "CASH", t))


def test_tsmom_holds_uptrend_only_and_vol_scales():
    w = tsmom_vol_scaled(_prices(), "CASH", _prices().index[500])
    assert w["UP"] > 0 and w["DOWN"] == 0
    assert w["UP"] <= 0.5 + 1e-12                       # 1/N cap with N=2


def test_execution_is_next_day_and_costs_charged():
    p = _prices()
    me = [d for d in month_ends(p.index) if d >= p.index[300]]
    res = run(p, "CASH", fixed({"UP": 1.0}), start=p.index[300], cost_bp=10.0)
    first_exec = res.weights.index[0]
    assert first_exec == p.index[p.index > me[0]][0]   # day after the first month-end
    assert res.returns.loc[first_exec] < 0              # only the cost hits that day
    assert abs(res.returns.loc[first_exec] + 2 * 1.0 * 10 / 1e4) < 1e-12
    nxt = p.index[p.index > first_exec][0]
    assert abs(res.returns.loc[nxt] - p["UP"].pct_change().loc[nxt]) < 1e-12


def test_unallocated_weight_sits_in_cash():
    res = run(_prices(), "CASH", fixed({"UP": 0.3}), start=_prices().index[300])
    w = res.weights.iloc[0]
    assert abs(w["CASH"] - 0.7) < 1e-12 and abs(w.sum() - 1.0) < 1e-12
