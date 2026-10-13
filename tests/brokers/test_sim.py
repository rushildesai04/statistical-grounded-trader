"""SimBroker behavior: no look-ahead, fill rules, participation, halts, settlement."""
from __future__ import annotations

from datetime import timedelta

import pytest

from sgt.brokers import CostModel, SimBroker
from sgt.core.types import (
    HaltEvent,
    HaltState,
    OrderStatus,
    OrderType,
    Segment,
    Side,
    TimeInForce,
)

from brk_helpers import SESSION, make_bar, make_intent

# A zero-cost model makes fill prices exact and easy to assert.
ZERO = CostModel(hs_under_1=0, hs_1_to_5=0, hs_5_to_20=0, hs_over_20=0,
                 per_share_fee=0, per_order_fee=0)


def _buy(cid="b1", **kw):
    return make_intent(cid=cid, side=Side.BUY, **kw)


def test_no_same_bar_fill_then_next_bar():
    # An order submitted while bar t is the latest processed bar must not fill on a
    # bar that is still "bar t"; it becomes eligible on the next processed bar.
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    sim.on_bar(make_bar(minute=570))        # process bar t (seq 0 -> 1)
    i = _buy(order_type=OrderType.MARKET, limit_price=None)
    sim.submit_sync(i)                       # submit_seq = 1 (after bar t)
    f = sim.on_bar(make_bar(minute=571))     # next bar t+1 -> eligible, fills
    assert len(f) == 1 and f[0].qty == 10

    # And an order submitted before any bar fills on the very first processed bar,
    # but not before: prove no fill is produced for a bar that predates eligibility.
    sim2 = SimBroker("acct", ZERO, 1_000_000.0)
    sim2.submit_sync(_buy(cid="b2", order_type=OrderType.MARKET, limit_price=None))
    assert len(sim2.on_bar(make_bar(minute=570))) == 1


def test_market_fills_at_open():
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    i = _buy(order_type=OrderType.MARKET, limit_price=None)
    sim.submit_sync(i)
    f = sim.on_bar(make_bar(minute=570, open=5.0))
    assert f[0].price == 5.0


def test_buy_limit_fills_at_limit_when_open_above_but_low_crosses():
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    i = _buy(limit_price=5.0)
    sim.submit_sync(i)
    # open 5.2 > limit 5.0 (no open fill); low 4.9 <= 5.0 -> conservative fill AT limit
    f = sim.on_bar(make_bar(open=5.2, high=5.3, low=4.9, close=5.1))
    assert len(f) == 1 and f[0].price == 5.0


def test_buy_limit_stays_working_when_untouched():
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    i = _buy(limit_price=4.5)
    sim.submit_sync(i)
    f = sim.on_bar(make_bar(open=5.2, high=5.3, low=5.0, close=5.1))
    assert f == []


def test_stop_sell_gap_through():
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    # First build a position with a buy.
    sim.submit_sync(_buy(cid="pos", order_type=OrderType.MARKET, limit_price=None))
    sim.on_bar(make_bar(minute=570, open=5.0))
    # Stop sell at 4.8; next bar gaps down, open 4.5 below stop -> fills at min(open,stop)=4.5.
    stop = make_intent(cid="stop", side=Side.SELL, order_type=OrderType.STOP,
                       limit_price=None, stop_price=4.8)
    sim.submit_sync(stop)
    f = sim.on_bar(make_bar(minute=571, open=4.5, high=4.6, low=4.3, close=4.4))
    assert len(f) == 1 and f[0].side is Side.SELL and f[0].price == 4.5


def test_participation_partial_fill():
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    # cap = 1% of 1000 = 10 shares/bar; order 25 -> 10, 10, 5.
    i = _buy(qty=25, order_type=OrderType.MARKET, limit_price=None)
    sim.submit_sync(i)
    f1 = sim.on_bar(make_bar(minute=570, volume=1000))
    f2 = sim.on_bar(make_bar(minute=571, volume=1000))
    f3 = sim.on_bar(make_bar(minute=572, volume=1000))
    assert [x.qty for x in f1 + f2 + f3] == [10, 10, 5]
    assert sum(x.qty for x in f1 + f2 + f3) == 25


def test_ext_hours_gating():
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    # Non-ext order does not fill on a PRE bar.
    i = _buy(limit_price=5.5)
    sim.submit_sync(i)
    f_pre = sim.on_bar(make_bar(minute=540, segment=Segment.PRE))
    assert f_pre == []
    # Ext-hours order fills on a PRE bar.
    sim2 = SimBroker("acct", ZERO, 1_000_000.0)
    ie = _buy(cid="ext", limit_price=5.5, extended_hours=True)
    sim2.submit_sync(ie)
    f = sim2.on_bar(make_bar(minute=540, segment=Segment.PRE))
    assert len(f) == 1


def test_day_order_expires_at_session_end():
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    i = _buy(limit_price=4.0, tif=TimeInForce.DAY)  # never crosses
    sim.submit_sync(i)
    sim.on_bar(make_bar(open=5.0, low=4.5))
    sim.end_session(SESSION)
    assert sim._orders[i.client_order_id].status is OrderStatus.EXPIRED


def test_ioc_expires_after_one_eligible_bar():
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    i = _buy(qty=25, order_type=OrderType.MARKET, limit_price=None, tif=TimeInForce.IOC)
    sim.submit_sync(i)
    f = sim.on_bar(make_bar(minute=570, volume=1000))  # cap 10 -> partial
    assert len(f) == 1 and f[0].qty == 10
    assert sim._orders[i.client_order_id].status is OrderStatus.EXPIRED
    f2 = sim.on_bar(make_bar(minute=571, volume=1000))
    assert f2 == []


def test_halt_blocks_then_resume_fills_at_open():
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    i = _buy(order_type=OrderType.MARKET, limit_price=None)
    sim.submit_sync(i)
    sim.on_halt(HaltEvent("AAA", make_bar().ts, HaltState.HALTED))
    f_halted = sim.on_bar(make_bar(minute=570, open=5.0))
    assert f_halted == []
    sim.on_halt(HaltEvent("AAA", make_bar(minute=571).ts, HaltState.RESUMED))
    f = sim.on_bar(make_bar(minute=572, open=6.0))  # gap up realized
    assert len(f) == 1 and f[0].price == 6.0


def test_settled_cash_rejection_cash_account():
    sim = SimBroker("acct", ZERO, 100.0, cash_account=True)
    # 30 shares * $5 = $150 > $100 settled -> rejected at submit.
    i = _buy(qty=30, limit_price=5.0)
    ack = sim.submit_sync(i)
    assert ack.status is OrderStatus.REJECTED
    f = sim.on_bar(make_bar())
    assert f == []


def test_sell_settlement_timing():
    sim = SimBroker("acct", ZERO, 1000.0, settle_days=1, cash_account=True)
    # Buy 100 @ $5 using settled cash.
    sim.submit_sync(_buy(cid="buy", qty=100, order_type=OrderType.MARKET, limit_price=None))
    sim.on_bar(make_bar(minute=570, open=5.0, volume=1_000_000))
    assert sim.settled_cash == pytest.approx(500.0)
    # Sell 100 @ $6: cash rises immediately, settled cash does NOT until T+1.
    sim.submit_sync(make_intent(cid="sell", side=Side.SELL, order_type=OrderType.MARKET,
                                limit_price=None, qty=100))
    sim.on_bar(make_bar(minute=571, open=6.0, volume=1_000_000))
    assert sim.cash == pytest.approx(500.0 + 600.0)
    assert sim.settled_cash == pytest.approx(500.0)  # proceeds unsettled
    sim.end_session(SESSION)  # +1 session -> settles
    assert sim.settled_cash == pytest.approx(1100.0)


def test_deterministic_fill_ids():
    def run():
        sim = SimBroker("acct", ZERO, 1_000_000.0)
        i = _buy(cid="det", qty=25, order_type=OrderType.MARKET, limit_price=None)
        sim.submit_sync(i)
        ids = []
        for m in (570, 571, 572):
            ids += [f.fill_id for f in sim.on_bar(make_bar(minute=m, volume=1000))]
        return ids
    assert run() == run() == ["det-f0", "det-f1", "det-f2"]


def test_explicit_end_session_then_autoroll_no_double():
    from datetime import date
    sim = SimBroker("acct", ZERO, 1000.0, settle_days=1, cash_account=True)
    sim.submit_sync(_buy(cid="b", qty=100, order_type=OrderType.MARKET, limit_price=None))
    sim.on_bar(make_bar(minute=570, open=5.0, volume=10_000_000))  # settled -> 500
    sim.submit_sync(make_intent(cid="s", side=Side.SELL, order_type=OrderType.MARKET,
                                limit_price=None, qty=100))
    sim.on_bar(make_bar(minute=571, open=6.0, volume=10_000_000))  # +600 cash, unsettled
    day = _buy(cid="day", limit_price=1.0, tif=TimeInForce.DAY)    # never fills
    sim.submit_sync(day)
    sim.on_bar(make_bar(minute=572, open=6.0, volume=10_000_000))
    assert sim.settled_cash == pytest.approx(500.0)

    # Engine calls end_session explicitly; proceeds settle once (T+1), DAY expires once.
    sim.end_session(SESSION)
    assert sim.settled_cash == pytest.approx(1100.0)
    assert sim._session_seq == 1
    assert sim._orders["day"].status is OrderStatus.EXPIRED

    # First bar of the next session auto-rolls — must NOT settle or expire again.
    next_session = date(2026, 10, 13)
    sim.on_bar(make_bar(minute=570, open=6.0, volume=10_000_000, session_date=next_session))
    assert sim.settled_cash == pytest.approx(1100.0)  # no double-settle
    assert sim._session_seq == 1                       # no double-advance


def test_sell_never_oversells_position():
    sim = SimBroker("acct", ZERO, 1_000_000.0)
    sim.submit_sync(_buy(cid="buy", qty=10, order_type=OrderType.MARKET, limit_price=None))
    sim.on_bar(make_bar(minute=570, open=5.0, volume=1_000_000))
    sim.submit_sync(make_intent(cid="sell", side=Side.SELL, order_type=OrderType.MARKET,
                                limit_price=None, qty=50))
    f = sim.on_bar(make_bar(minute=571, open=6.0, volume=1_000_000))
    assert f[0].qty == 10  # capped at held
