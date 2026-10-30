"""Passive rebalance planner: targets, buffer, drift threshold, sells-first, caps, calendar."""
from __future__ import annotations

from datetime import date

import pytest

from sgt.allocation.rebalance import first_trading_day, in_rebalance_days, plan, trading_day_of_month

PX = {"SPY": 500.0, "IEF": 100.0}


def test_initial_buy_from_cash_respects_buffer():
    orders = plan(cash=100_000, holdings={}, prices=PX)
    got = {o.symbol: o for o in orders}
    assert got["SPY"].qty == 118 and got["IEF"].qty == 396      # 0.99*100k*60%/500, *40%/100
    spend = sum(o.qty * o.limit for o in orders)
    assert spend <= 100_000 * 0.99 + 1e-6


def test_no_trade_inside_drift_threshold():
    assert plan(cash=1_000, holdings={"SPY": 119, "IEF": 396}, prices=PX) == []


def test_sells_first_and_buys_funded_by_sells():
    orders = plan(cash=0, holdings={"SPY": 200, "IEF": 0}, prices=PX)
    assert [o.side for o in orders] == ["sell", "buy"]
    sell, buy = orders
    assert buy.qty * buy.limit <= sell.notional - 100_000 * 0.01 + 1e-6


def test_other_holdings_are_never_touched():
    orders = plan(cash=100_000, holdings={"AAPL": 50}, prices=PX)
    assert {o.symbol for o in orders} == {"SPY", "IEF"}


def test_fail_closed_on_bad_inputs_and_caps():
    with pytest.raises(ValueError):
        plan(cash=100_000, holdings={}, prices={"SPY": 500.0})               # missing IEF
    with pytest.raises(ValueError):
        plan(cash=100_000, holdings={}, prices=PX, targets={"SPY": 0.7, "IEF": 0.4})
    with pytest.raises(ValueError):
        plan(cash=100_000, holdings={}, prices=PX, max_order_notional=10_000)


def test_first_trading_day():
    assert first_trading_day(date(2026, 10, 1))        # Thursday
    assert not first_trading_day(date(2026, 10, 2))
    assert first_trading_day(date(2026, 11, 2))        # Nov 1 is a Sunday
    assert not first_trading_day(date(2026, 11, 1))


def test_rebalance_window_first_five_sessions():
    assert trading_day_of_month(date(2026, 11, 2)) == 1         # Nov 1 is a Sunday
    assert trading_day_of_month(date(2026, 11, 6)) == 5
    assert in_rebalance_days(date(2026, 11, 6)) and not in_rebalance_days(date(2026, 11, 9))
    assert not in_rebalance_days(date(2026, 11, 7))              # Saturday
    assert trading_day_of_month(date(2026, 11, 27)) is not None  # half day still a session
