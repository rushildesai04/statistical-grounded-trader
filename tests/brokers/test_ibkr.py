"""IBKRBroker unit tests against an in-process fake IB client.

The fake implements the ``sgt.brokers.ibkr.IBClient`` protocol plus the
``Stock``/``LimitOrder``/``MarketOrder``/``StopOrder`` factories that ib_async exposes,
so none of these tests import ``ib_async`` or touch a gateway. The fake is also reused
by the shared contract suite via the ``ibkr`` harness in conftest.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from sgt.brokers.ibkr import IBKRBroker
from sgt.core.interfaces import (
    BrokerAuthError,
    BrokerRejectedError,
    BrokerUnavailableError,
)
from sgt.core.types import OrderType, Side

from brk_helpers import make_intent

UTC = timezone.utc


# --------------------------------------------------------------------------- fake IB
class FakeIB:
    """Faithful, in-memory stand-in for ib_async.IB (plus its object factories)."""

    def __init__(self, *, connect_succeeds=True, connect_raises=None):
        self._connected = False
        self._connect_succeeds = connect_succeeds
        self._connect_raises = connect_raises
        self.account_values: list[SimpleNamespace] = []
        self.positions_list: list[SimpleNamespace] = []
        self.open: list[SimpleNamespace] = []
        self.completed: list[SimpleNamespace] = []
        self.executions: list[SimpleNamespace] = []
        self.reject_refs: dict[str, tuple[int, str]] = {}  # orderRef -> (code, message)
        self.place_count = 0
        self.last_order: SimpleNamespace | None = None
        self.last_contract: SimpleNamespace | None = None
        self.canceled: list[str] = []
        self._perm = 1000

    # -- factories (mirror ib_async module-level constructors) --
    @staticmethod
    def Stock(symbol, exchange, currency, **kw):
        return SimpleNamespace(symbol=symbol, exchange=exchange, currency=currency,
                               secType="STK")

    @staticmethod
    def _order(action, qty, order_type, lmt, aux, kw):
        return SimpleNamespace(
            action=action, totalQuantity=qty, orderType=order_type, lmtPrice=lmt,
            auxPrice=aux, outsideRth=kw.get("outsideRth", False), tif=kw.get("tif", "DAY"),
            orderRef=kw.get("orderRef", ""), account=kw.get("account", ""),
            permId=0, orderId=0)

    @staticmethod
    def LimitOrder(action, qty, lmt, **kw):
        return FakeIB._order(action, qty, "LMT", lmt, 0.0, kw)

    @staticmethod
    def MarketOrder(action, qty, **kw):
        return FakeIB._order(action, qty, "MKT", 0.0, 0.0, kw)

    @staticmethod
    def StopOrder(action, qty, stop, **kw):
        return FakeIB._order(action, qty, "STP", 0.0, stop, kw)

    # -- connection --
    async def connectAsync(self, host, port, clientId, **kw):
        if self._connect_raises is not None:
            raise self._connect_raises
        self._connected = bool(self._connect_succeeds)

    def isConnected(self):
        return self._connected

    def disconnect(self):
        self._connected = False

    # -- account --
    async def accountSummaryAsync(self, account=""):
        return self.account_values

    async def reqPositionsAsync(self):
        return self.positions_list

    # -- orders --
    def placeOrder(self, contract, order):
        self.place_count += 1
        self.last_order = order
        self.last_contract = contract
        self._perm += 1
        order.permId = self._perm
        reject = self.reject_refs.get(order.orderRef)
        if reject is not None:
            code, msg = reject
            status = SimpleNamespace(status="Inactive", filled=0.0, remaining=0.0)
            logentry = SimpleNamespace(time=datetime(2026, 10, 12, 14, 31, tzinfo=UTC),
                                       status="Inactive", message=msg, errorCode=code)
            trade = SimpleNamespace(contract=contract, order=order, orderStatus=status,
                                    log=[logentry], advancedError="")
        else:
            status = SimpleNamespace(status="Submitted", filled=0.0,
                                     remaining=float(order.totalQuantity))
            trade = SimpleNamespace(contract=contract, order=order, orderStatus=status,
                                    log=[], advancedError="")
            self.open.append(trade)
        return trade

    def cancelOrder(self, order):
        self.canceled.append(order.orderRef)
        for t in self.open:
            if t.order.orderRef == order.orderRef:
                t.orderStatus.status = "Cancelled"
                t.orderStatus.remaining = 0.0
        self.open = [t for t in self.open if t.order.orderRef != order.orderRef]
        return None

    def openTrades(self):
        return [t for t in self.open
                if t.orderStatus.status not in ("Filled", "Cancelled", "ApiCancelled", "Inactive")]

    async def reqCompletedOrdersAsync(self, apiOnly):
        return self.completed

    async def reqExecutionsAsync(self, execFilter=None):
        return self.executions

    # -- helpers for arranging test state --
    def add_execution(self, *, cid, symbol="AAA", side="BOT", shares=10, price=5.0,
                      exec_id="exec-1", commission=0.01):
        ex = SimpleNamespace(execId=exec_id, orderRef=cid, permId=1, orderId=1,
                             side=side, shares=shares, price=price,
                             time=datetime(2026, 10, 12, 14, 35, tzinfo=UTC))
        fill = SimpleNamespace(
            contract=SimpleNamespace(symbol=symbol, secType="STK"),
            execution=ex,
            commissionReport=SimpleNamespace(commission=commission),
            time=ex.time)
        self.executions.append(fill)

    def add_position(self, symbol, qty, avg_cost=5.0, account="DU111"):
        self.positions_list.append(SimpleNamespace(
            account=account,
            contract=SimpleNamespace(symbol=symbol, secType="STK"),
            position=qty, avgCost=avg_cost))


def make_broker(ib=None, *, paper=True):
    return IBKRBroker("acct", "DU111", paper=paper, ib=ib or FakeIB())


async def _connected_broker(ib=None):
    broker = make_broker(ib)
    await broker.connect()
    return broker


# --------------------------------------------------------------------------- tests
async def test_connect_requires_established_session():
    ib = FakeIB(connect_succeeds=False)
    broker = make_broker(ib)
    with pytest.raises(BrokerAuthError):
        await broker.connect()


async def test_connect_transport_failure_is_unavailable():
    ib = FakeIB(connect_raises=ConnectionRefusedError("refused"))
    broker = make_broker(ib)
    with pytest.raises(BrokerUnavailableError):
        await broker.connect()


async def test_order_ref_idempotency_no_second_place():
    ib = FakeIB()
    broker = await _connected_broker(ib)
    intent = make_intent(cid="idem-1")
    ack1 = await broker.submit(intent)
    ack2 = await broker.submit(intent)
    assert ib.place_count == 1
    assert ack1.broker_order_id == ack2.broker_order_id
    assert ack1.client_order_id == "idem-1"


async def test_extended_hours_market_rejected():
    broker = await _connected_broker()
    intent = make_intent(cid="eh-mkt", order_type=OrderType.MARKET, limit_price=None)
    object.__setattr__(intent, "extended_hours", True)  # bypass type guard to hit adapter
    with pytest.raises(BrokerRejectedError):
        await broker.submit(intent)


async def test_outside_rth_set_for_extended_hours_limit():
    ib = FakeIB()
    broker = await _connected_broker(ib)
    await broker.submit(make_intent(cid="eh-lim", extended_hours=True, limit_price=5.5))
    assert ib.last_order.outsideRth is True
    assert ib.last_order.orderType == "LMT"
    assert ib.last_order.orderRef == "eh-lim"


async def test_rth_limit_not_outside_rth():
    ib = FakeIB()
    broker = await _connected_broker(ib)
    await broker.submit(make_intent(cid="rth-lim", extended_hours=False))
    assert ib.last_order.outsideRth is False


async def test_stop_order_is_rth_broker_side():
    ib = FakeIB()
    broker = await _connected_broker(ib)
    ib.add_position("AAA", 10)  # STOP here is a SELL exit; needs a long position
    await broker.submit(make_intent(cid="stp", side=Side.SELL,
                                    order_type=OrderType.STOP, limit_price=None,
                                    stop_price=4.0))
    assert ib.last_order.orderType == "STP"
    assert ib.last_order.outsideRth is False
    assert ib.last_order.auxPrice == 4.0


async def test_fills_dedupe_across_polls():
    ib = FakeIB()
    broker = await _connected_broker(ib)
    ib.add_execution(cid="f1", exec_id="e-1")
    first = [f async for f in broker.fills()]
    second = [f async for f in broker.fills()]
    assert len(first) == 1 and second == []
    assert first[0].fill_id == "e-1"
    assert first[0].fee == pytest.approx(0.01)


async def test_disconnect_maps_to_unavailable():
    ib = FakeIB()
    broker = await _connected_broker(ib)
    ib.disconnect()
    with pytest.raises(BrokerUnavailableError):
        await broker.account_state()
    with pytest.raises(BrokerUnavailableError):
        await broker.submit(make_intent(cid="x"))


async def test_rejected_order_maps_to_rejected_error():
    ib = FakeIB()
    ib.reject_refs["rej-1"] = (201, "Order rejected - insufficient margin")
    broker = await _connected_broker(ib)
    with pytest.raises(BrokerRejectedError):
        await broker.submit(make_intent(cid="rej-1"))


async def test_sell_exceeding_position_rejected():
    ib = FakeIB()
    broker = await _connected_broker(ib)  # no positions -> available 0
    with pytest.raises(BrokerRejectedError):
        await broker.submit(make_intent(cid="sell-1", side=Side.SELL))


async def test_sell_within_position_allowed():
    ib = FakeIB()
    broker = await _connected_broker(ib)
    ib.add_position("AAA", 10)
    ack = await broker.submit(make_intent(cid="sell-ok", side=Side.SELL, qty=10))
    assert ack.side is Side.SELL
    assert ib.place_count == 1


async def test_sell_counts_working_sells_against_position():
    ib = FakeIB()
    broker = await _connected_broker(ib)
    ib.add_position("AAA", 10)
    await broker.submit(make_intent(cid="sell-a", side=Side.SELL, qty=6))  # 6 working
    with pytest.raises(BrokerRejectedError):  # 6 + 6 > 10 available
        await broker.submit(make_intent(cid="sell-b", side=Side.SELL, qty=6))


async def test_account_state_maps_summary_tags():
    ib = FakeIB()
    ib.account_values = [
        SimpleNamespace(account="DU111", tag="TotalCashValue", value="2500", currency="USD"),
        SimpleNamespace(account="DU111", tag="SettledCash", value="2000", currency="USD"),
        SimpleNamespace(account="DU111", tag="BuyingPower", value="10000", currency="USD"),
    ]
    ib.add_position("AAA", 5, avg_cost=4.2)
    broker = await _connected_broker(ib)
    state = await broker.account_state()
    assert state.cash == 2500.0
    assert state.settled_cash == 2000.0
    assert state.buying_power == 10000.0
    assert state.positions[0].symbol == "AAA" and state.positions[0].qty == 5


def test_paper_flag_with_live_port_refused(monkeypatch):
    monkeypatch.setenv("SGT_SECRET_IBKR_PORT", "4001")
    with pytest.raises(ValueError):
        IBKRBroker("acct", "DU111", paper=True, ib=FakeIB())


def test_live_flag_with_paper_port_refused(monkeypatch):
    monkeypatch.setenv("SGT_SECRET_IBKR_PORT", "4002")
    with pytest.raises(ValueError):
        IBKRBroker("acct", "DU111", paper=False, ib=FakeIB())
