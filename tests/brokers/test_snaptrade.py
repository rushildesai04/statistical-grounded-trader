"""SnapTrade adapter tests: signing vector, fail-closed read-only, idempotent reconcile,
rate-limit spacing, positions mapping, and secret hygiene. No network (respx)."""
from __future__ import annotations

import logging

import httpx
import pytest
import respx

from sgt.accounts.models import OrderLedger
from sgt.brokers.snaptrade import (
    SnapTradeBroker,
    account_state_from,
    positions_from_json,
)
from sgt.brokers.snaptrade_client import SnapTradeClient, compute_signature
from sgt.core.interfaces import BrokerRejectedError, BrokerUnavailableError
from sgt.core.types import Side
from sgt.db.session import get_session

from brk_helpers import make_intent
from conftest import snaptrade_order_record

BASE = "https://api.snaptrade.com"
ST_ACCT = "st-acct-1"
AUTH_ID = "auth-1"


def _client(**kw) -> SnapTradeClient:
    kw.setdefault("client_id", "test-client")
    kw.setdefault("consumer_key", "test-consumer-key")
    kw.setdefault("user_id", "user-123")
    kw.setdefault("user_secret", "sec-xyz")
    kw.setdefault("trade_min_interval_s", 0.0)
    return SnapTradeClient(**kw)


def _register_base(r, *, conn_type="trade", orders_box=None):
    orders_box = orders_box if orders_box is not None else []
    r.get("/api/v1/").mock(return_value=httpx.Response(200, json={"online": True}))
    r.get(f"/api/v1/authorizations/{AUTH_ID}").mock(return_value=httpx.Response(
        200, json={"id": AUTH_ID, "type": conn_type, "disabled": False}))
    r.get("/api/v1/accounts/all").mock(return_value=httpx.Response(200, json=[
        {"id": ST_ACCT, "brokerage_authorization": AUTH_ID,
         "institution_name": "TestBroker", "number": "X1", "is_paper": False}]))
    r.get(f"/api/v1/accounts/{ST_ACCT}/balances").mock(return_value=httpx.Response(
        200, json=[{"currency": {"code": "USD"}, "cash": 1000.0, "buying_power": 1000.0}]))
    r.get(f"/api/v1/accounts/{ST_ACCT}/positions/all").mock(return_value=httpx.Response(200, json=[]))
    r.get(f"/api/v1/accounts/{ST_ACCT}/orders").mock(
        side_effect=lambda request: httpx.Response(200, json=orders_box))
    return orders_box


# --------------------------------------------------------------------------- signing
def test_signature_matches_hand_computed_vector():
    # Hand-computed with SnapTrade's documented canonical-JSON + HMAC-SHA256 scheme.
    sig1 = compute_signature(
        "/snapTrade/registerUser", "clientId=test-client&timestamp=1700000000",
        "test-consumer-key", {"userId": "user-123"})
    assert sig1 == "8o/OJEmfU2waYLiGzfWWDZQlnrGxBbL0m5MS9/rJ+go="

    sig2 = compute_signature(
        "/accounts/acc-1/positions/all",
        "clientId=test-client&timestamp=1700000000&userId=user-123&userSecret=sec-xyz",
        "test-consumer-key", None)
    assert sig2 == "mihkfEQ6YtS4X5ghu7QDEx66Dc6fKsdgNA4bNsfTHEQ="


async def test_signature_is_sent_as_header_with_user_scoped_query():
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        route = r.get("/api/v1/accounts/all").mock(return_value=httpx.Response(200, json=[]))
        client = _client()
        await client.list_accounts()
        await client.close()
        req = route.calls.last.request
        assert req.headers["Signature"]
        url = str(req.url)
        assert "clientId=test-client" in url and "timestamp=" in url
        assert "userId=user-123" in url and "userSecret=sec-xyz" in url


# --------------------------------------------------------------------------- fail closed
async def test_read_only_connection_refuses_submit_and_cancel():
    broker = SnapTradeBroker("acct", ST_ACCT, trade_enabled=False, client=_client())
    await broker.connect()  # no-op for read-only
    with pytest.raises(BrokerRejectedError, match="read-only connection"):
        await broker.submit(make_intent(cid="ro-1"))
    with pytest.raises(BrokerRejectedError, match="read-only connection"):
        await broker.cancel("ro-1")
    await broker.close()


async def test_trade_enabled_but_read_connection_fails_closed_on_connect():
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _register_base(r, conn_type="read")
        broker = SnapTradeBroker("acct", ST_ACCT, trade_enabled=True,
                                 authorization_id=AUTH_ID, client=_client())
        with pytest.raises(BrokerRejectedError, match="not trade-capable"):
            await broker.connect()
        # Even after the failed verification, submit stays fail-closed.
        with pytest.raises(BrokerRejectedError, match="read-only connection"):
            await broker.submit(make_intent(cid="x"))
        await broker.close()


# --------------------------------------------------------------------------- idempotency
async def test_timeout_landed_order_is_adopted_without_duplicate():
    place_calls = {"n": 0}
    orders = [snaptrade_order_record("boid-existing", symbol="AAA", action="BUY", qty=10,
                                     status="ACCEPTED", limit_price=5.5)]
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _register_base(r, orders_box=orders)

        def place(request):
            place_calls["n"] += 1
            raise httpx.TimeoutException("boom")
        r.post("/api/v1/trade/place").mock(side_effect=place)

        broker = SnapTradeBroker("acct", ST_ACCT, trade_enabled=True,
                                 authorization_id=AUTH_ID, client=_client())
        await broker.connect()
        ack = await broker.submit(make_intent(cid="idem-1", limit_price=5.5))
        assert ack.broker_order_id == "boid-existing"
        assert place_calls["n"] == 1  # reconciled, never resubmitted
        await broker.close()


async def test_ambiguous_reconcile_marks_unknown_and_blocks_resubmit():
    orders = [snaptrade_order_record("boid-a", limit_price=5.5),
              snaptrade_order_record("boid-b", limit_price=5.5)]
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _register_base(r, orders_box=orders)
        r.post("/api/v1/trade/place").mock(side_effect=httpx.TimeoutException("boom"))

        broker = SnapTradeBroker("acct", ST_ACCT, trade_enabled=True,
                                 authorization_id=AUTH_ID, client=_client())
        await broker.connect()
        with pytest.raises(BrokerUnavailableError, match="ambiguous"):
            await broker.submit(make_intent(cid="amb-1", limit_price=5.5))

        with get_session() as session:
            row = session.get(OrderLedger, "amb-1")
            assert row is not None and row.status == "unknown"

        # A later submit of the same id must stay blocked (manual resolution required).
        with pytest.raises(BrokerUnavailableError, match="unknown"):
            await broker.submit(make_intent(cid="amb-1", limit_price=5.5))
        await broker.close()


async def test_long_only_rejects_oversell_client_side():
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _register_base(r)
        # Report a 3-share position; a SELL of 10 must be rejected before any POST.
        r.get(f"/api/v1/accounts/{ST_ACCT}/positions/all").mock(return_value=httpx.Response(
            200, json=[{"symbol": {"symbol": {"symbol": "AAA"}}, "units": 3,
                        "average_purchase_price": 4.0}]))
        place = r.post("/api/v1/trade/place").mock(return_value=httpx.Response(
            200, json=snaptrade_order_record("boid-1")))
        broker = SnapTradeBroker("acct", ST_ACCT, trade_enabled=True,
                                 authorization_id=AUTH_ID, client=_client())
        await broker.connect()
        with pytest.raises(BrokerRejectedError, match="long-only"):
            await broker.submit(make_intent(cid="sell-1", side=Side.SELL, qty=10,
                                            limit_price=5.5))
        assert not place.called
        await broker.close()


# --------------------------------------------------------------------------- rate limiting
async def test_trade_requests_are_spaced_one_per_second():
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def mono():
        return clock["t"]

    async def sleep(s):
        sleeps.append(s)
        clock["t"] += s

    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _register_base(r)
        r.post("/api/v1/trade/place").mock(return_value=httpx.Response(
            200, json=snaptrade_order_record("boid-1")))
        client = _client(trade_min_interval_s=1.0, monotonic=mono, sleep=sleep)
        # Two back-to-back trade requests: the second waits exactly one interval.
        await client.place_force_order({"account_id": ST_ACCT, "action": "BUY",
                                        "order_type": "Market", "time_in_force": "Day",
                                        "symbol": "AAA", "units": 1})
        await client.place_force_order({"account_id": ST_ACCT, "action": "BUY",
                                        "order_type": "Market", "time_in_force": "Day",
                                        "symbol": "AAA", "units": 1})
        await client.close()
    assert sleeps == [1.0]


# --------------------------------------------------------------------------- mapping
def test_positions_and_account_state_mapping():
    positions = [
        {"symbol": {"symbol": {"symbol": "aaa"}}, "units": 10, "average_purchase_price": 4.25},
        {"symbol": {"symbol": {"symbol": "BBB"}}, "units": 0, "average_purchase_price": 9.0},
        {"symbol": {"symbol": {"symbol": "CCC"}}, "units": 5, "price": 12.0},
    ]
    mapped = positions_from_json(positions)
    assert [(p.symbol, p.qty, p.avg_price) for p in mapped] == [("AAA", 10, 4.25), ("CCC", 5, 12.0)]

    balances = [{"currency": {"code": "USD"}, "cash": 1000.0, "buying_power": 1500.0},
                {"currency": {"code": "CAD"}, "cash": 999.0, "buying_power": 999.0}]
    state = account_state_from("acct", balances, positions)
    assert state.cash == 1000.0 and state.buying_power == 1500.0
    assert state.account_id == "acct"
    assert {p.symbol for p in state.positions} == {"AAA", "CCC"}


# --------------------------------------------------------------------------- secret hygiene
async def test_secrets_never_logged(caplog):
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        r.get("/api/v1/accounts/all").mock(side_effect=httpx.ConnectError("down"))
        client = _client(consumer_key="SUPER-SECRET-CONSUMER", user_secret="SUPER-SECRET-USER")
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(BrokerUnavailableError):
                await client.call("GET", "/accounts/all", retry=False)
        await client.close()
    text = caplog.text
    assert "SUPER-SECRET-CONSUMER" not in text
    assert "SUPER-SECRET-USER" not in text
