import pandas as pd

from sgt.backtest.metrics import accuracy, base_rate, traded_pnl


def test_base_rate():
    y = pd.Series(["up", "up", "up", "down"])
    assert base_rate(y) == 0.75


def test_accuracy():
    assert accuracy(["up", "down"], ["up", "up"]) == 0.5


def test_traded_pnl_charges_costs():
    # one 'up' with +5% return, cost 1% -> net 4%; one 'flat' -> no trade.
    stats = traded_pnl(["up", "flat"], [0.05, 0.10], cost=0.01)
    assert stats["n_trades"] == 1
    assert abs(stats["net_pnl"] - 0.04) < 1e-9
    assert stats["win_rate_on_trades"] == 1.0


def test_traded_pnl_short():
    # 'down' pred with -5% return profits when short, minus cost.
    stats = traded_pnl(["down"], [-0.05], cost=0.01)
    assert abs(stats["net_pnl"] - 0.04) < 1e-9


def test_flat_never_trades():
    stats = traded_pnl(["flat", "flat"], [0.9, -0.9], cost=0.01)
    assert stats["n_trades"] == 0
    assert stats["net_pnl"] == 0.0
