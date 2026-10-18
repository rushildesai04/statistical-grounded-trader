"""Regression tests for the real-money broker-adapter defects (F1-F6 + VERIFY items).

Each test is written to FAIL against the pre-fix code and pass after the fix:
- F1 SnapTrade crash-restart reconciles before any POST (no duplicate order).
- F2 Schwab reconcile window is anchored on the intent, and refuses when un-queryable.
- F3 long-only subtracts shares already working in other SELL orders (Schwab + SnapTrade).
- F4 SnapTrade resubmits at most once after a confident zero-match, then marks unknown.
- F5 Schwab shares ONE token manager per app key; concurrent refresh => one token POST.
- F6 Schwab maps IOC to IMMEDIATE_OR_CANCEL (partial-fill semantics), not FILL_OR_KILL.
- VERIFY SnapTrade order-symbol shapes and cancel-not-confirmed-terminal behavior.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from sgt.accounts.ledger import record_ack
from sgt.accounts.models import OrderLedger
from sgt.brokers import SchwabBroker, SchwabTokenManager, SnapTradeBroker
from sgt.brokers import schwab_mapping as smap
from sgt.brokers.schwab_ledger import UNKNOWN as _UNKNOWN, ledger_mark
from sgt.core.interfaces import BrokerRejectedError, BrokerUnavailableError
from sgt.core.types import OrderAck, OrderStatus, OrderType, Side, TimeInForce
from sgt.db.session import get_session

from brk_helpers import make_intent
from conftest import snaptrade_order_record
from test_schwab import BASE as SCHWAB_BASE, _FakeSecrets, _fixed_clock, _make_broker, _token_ok
from test_snaptrade import AUTH_ID, ST_ACCT, _client as _st_client, _register_base

BASE_ST = "https://api.snaptrade.com"
UTC = timezone.utc


def _landed_order(entered: datetime, oid: str = "landed-1") -> dict:
    return {"orderId": oid, "status": "WORKING", "price": 5.5,
            "enteredTime": smap.fmt_entered(entered),
            "orderLegCollection": [{"instruction": "BUY", "quantity": 10,
                                    "instrument": {"symbol": "AAA"}}]}


# --------------------------------------------------------------------------- F6
def test_f6_schwab_ioc_maps_to_immediate_or_cancel():
    intent = make_intent(cid="ioc-1", tif=TimeInForce.IOC, limit_price=5.5)
    body = smap.order_body(intent, "NORMAL")
    assert body["duration"] == "IMMEDIATE_OR_CANCEL"  # not FILL_OR_KILL (all-or-none)


# --------------------------------------------------------------------------- F1
async def test_f1_snaptrade_crash_restart_reconciles_before_posting():
    created = datetime(2026, 10, 12, 14, 30, tzinfo=UTC)
    place_calls = {"n": 0}
    landed = snaptrade_order_record("boid-landed", symbol="AAA", action="BUY", qty=10,
                                    status="ACCEPTED", limit_price=5.5, time_placed=created)
    with respx.mock(base_url=BASE_ST, assert_all_called=False) as r:
        _register_base(r, orders_box=[landed])

        def place(request):
            place_calls["n"] += 1
            return httpx.Response(200, json=snaptrade_order_record("boid-dupe"))
        r.post("/api/v1/trade/place").mock(side_effect=place)

        broker = SnapTradeBroker("acct", ST_ACCT, trade_enabled=True,
                                 authorization_id=AUTH_ID, client=_st_client())
        await broker.connect()
        # A prior attempt pre-registered the intent then crashed: PENDING row, no broker id.
        seed = make_intent(cid="crash-1", limit_price=5.5)
        with get_session() as s:
            record_ack(s, OrderAck("crash-1", "", OrderStatus.PENDING, created, "pre",
                                   symbol="AAA", side=Side.BUY, remaining_qty=10), seed)
        ack = await broker.submit(make_intent(cid="crash-1", limit_price=5.5))
        assert ack.broker_order_id == "boid-landed"  # adopted the landed order
        assert place_calls["n"] == 0                  # never POSTed a duplicate
        await broker.close()


# --------------------------------------------------------------------------- F2
async def test_f2_schwab_reconcile_window_anchored_to_intent():
    store = _FakeSecrets()
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    created = now - timedelta(days=30)        # inside the 60d query window
    posts = {"n": 0}

    def post(request):
        posts["n"] += 1
        return httpx.Response(201, headers={
            "Location": f"{SCHWAB_BASE}/trader/v1/accounts/HASH/orders/999"})

    def get_orders(request):
        frm = smap.parse_ts(request.url.params["fromEnteredTime"])
        to = smap.parse_ts(request.url.params["toEnteredTime"])
        if frm <= created <= to:              # broker honours the entered-time window
            return httpx.Response(200, json=[_landed_order(created)])
        return httpx.Response(200, json=[])

    with respx.mock(base_url=SCHWAB_BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.post("/trader/v1/accounts/HASH/orders").mock(side_effect=post)
        r.get("/trader/v1/accounts/HASH/orders").mock(side_effect=get_orders)
        broker = _make_broker(store, clock=_fixed_clock(now))
        old = make_intent(cid="c-anchor", limit_price=5.5)
        object.__setattr__(old, "created_at", created)
        ledger_mark(old, "submitting", None, created)
        ack = await broker.submit(make_intent(cid="c-anchor", limit_price=5.5))
        assert ack.broker_order_id == "landed-1"  # found via anchored window
        assert posts["n"] == 0                     # never resubmitted
        await broker.close()


async def test_f2_schwab_refuses_reconcile_when_older_than_query_window():
    store = _FakeSecrets()
    now = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)
    created = now - timedelta(days=90)        # beyond the 60d query window
    posts = {"n": 0}

    def post(request):
        posts["n"] += 1
        return httpx.Response(201, headers={
            "Location": f"{SCHWAB_BASE}/trader/v1/accounts/HASH/orders/999"})

    def get_orders(request):
        frm = smap.parse_ts(request.url.params["fromEnteredTime"])
        to = smap.parse_ts(request.url.params["toEnteredTime"])
        if frm <= created <= to:
            return httpx.Response(200, json=[_landed_order(created)])
        return httpx.Response(200, json=[])

    with respx.mock(base_url=SCHWAB_BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.post("/trader/v1/accounts/HASH/orders").mock(side_effect=post)
        r.get("/trader/v1/accounts/HASH/orders").mock(side_effect=get_orders)
        broker = _make_broker(store, clock=_fixed_clock(now))
        old = make_intent(cid="c-old", limit_price=5.5)
        object.__setattr__(old, "created_at", created)
        ledger_mark(old, "submitting", None, created)
        with pytest.raises(BrokerUnavailableError):
            await broker.submit(make_intent(cid="c-old", limit_price=5.5))
        assert posts["n"] == 0  # never resubmitted a stale, un-queryable intent
        with get_session() as s:
            assert s.get(OrderLedger, "c-old").status == _UNKNOWN
        await broker.close()


# --------------------------------------------------------------------------- F3
async def test_f3_schwab_working_sell_subtracted_from_long_only():
    store = _FakeSecrets()
    posts = {"n": 0}

    def get_orders(request):  # a full-size working STOP sell already open
        return httpx.Response(200, json=[{
            "orderId": "stop-1", "status": "WORKING",
            "enteredTime": "2026-10-18T13:31:00.000Z",
            "filledQuantity": 0, "remainingQuantity": 10,
            "orderLegCollection": [{"instruction": "SELL", "quantity": 10,
                                    "instrument": {"symbol": "AAA"}}]}])

    with respx.mock(base_url=SCHWAB_BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.get("/trader/v1/accounts/HASH").mock(return_value=httpx.Response(200, json={
            "securitiesAccount": {"currentBalances": {"cashBalance": 0.0, "buyingPower": 0.0},
                                  "positions": [{"instrument": {"symbol": "AAA"},
                                                 "longQuantity": 10, "averagePrice": 4.0}]}}))
        r.get("/trader/v1/accounts/HASH/orders").mock(side_effect=get_orders)
        r.post("/trader/v1/accounts/HASH/orders").mock(
            side_effect=lambda req: (posts.__setitem__("n", posts["n"] + 1),
                                     httpx.Response(201))[1])
        broker = _make_broker(store)
        # held=10 but 10 already working in the STOP -> available 0 -> exit SELL rejected.
        with pytest.raises(BrokerRejectedError, match="long-only"):
            await broker.submit(make_intent(cid="exit-1", side=Side.SELL, qty=10,
                                            limit_price=5.5))
        assert posts["n"] == 0
        await broker.close()


async def test_f3_snaptrade_working_sell_subtracted_from_long_only():
    stop = snaptrade_order_record("boid-stop", symbol="AAA", action="SELL", qty=10,
                                  status="ACCEPTED")
    with respx.mock(base_url=BASE_ST, assert_all_called=False) as r:
        _register_base(r, orders_box=[stop])
        r.get(f"/api/v1/accounts/{ST_ACCT}/positions/all").mock(return_value=httpx.Response(
            200, json=[{"symbol": {"symbol": {"symbol": "AAA"}}, "units": 10,
                        "average_purchase_price": 4.0}]))
        place = r.post("/api/v1/trade/place").mock(return_value=httpx.Response(
            200, json=snaptrade_order_record("boid-x")))
        broker = SnapTradeBroker("acct", ST_ACCT, trade_enabled=True,
                                 authorization_id=AUTH_ID, client=_st_client())
        await broker.connect()
        with pytest.raises(BrokerRejectedError, match="long-only"):
            await broker.submit(make_intent(cid="exit-1", side=Side.SELL, qty=10,
                                            limit_price=5.5))
        assert not place.called
        await broker.close()


# --------------------------------------------------------------------------- F4
async def test_f4_snaptrade_resubmits_once_then_marks_unknown():
    place_calls = {"n": 0}
    with respx.mock(base_url=BASE_ST, assert_all_called=False) as r:
        _register_base(r, orders_box=[])  # reconciliation always finds zero matches

        def place(request):
            place_calls["n"] += 1
            raise httpx.TimeoutException("boom")
        r.post("/api/v1/trade/place").mock(side_effect=place)

        broker = SnapTradeBroker("acct", ST_ACCT, trade_enabled=True,
                                 authorization_id=AUTH_ID, client=_st_client())
        await broker.connect()
        with pytest.raises(BrokerUnavailableError):
            await broker.submit(make_intent(cid="loop-1", limit_price=5.5))
        assert place_calls["n"] == 2  # original POST + exactly one resubmit
        with get_session() as s:
            assert s.get(OrderLedger, "loop-1").status == "unknown"
        await broker.close()


# --------------------------------------------------------------------------- F5
async def test_f5_schwab_shared_manager_single_post_under_concurrent_refresh():
    SchwabTokenManager._reset_registry_for_tests()
    try:
        store = _FakeSecrets()
        posts = {"n": 0}

        def tok(request):
            posts["n"] += 1
            return httpx.Response(200, json={"access_token": "AT-NEW",
                                             "refresh_token": "RT-NEW", "expires_in": 1800})

        with respx.mock(base_url=SCHWAB_BASE, assert_all_called=False) as r:
            r.post("/v1/oauth/token").mock(side_effect=tok)
            m1 = SchwabTokenManager.shared(secret_get=store.get, secret_set=store.set)
            m2 = SchwabTokenManager.shared(secret_get=store.get, secret_set=store.set)
            assert m1 is m2  # one manager per app key
            t1, t2 = await asyncio.gather(m1.access_token(), m2.access_token())
            assert t1 == t2 == "AT-NEW"
            assert posts["n"] == 1  # exactly one token POST across concurrent refreshes
            await m1.close()
    finally:
        SchwabTokenManager._reset_registry_for_tests()


# --------------------------------------------------------------------------- VERIFY items
def test_verify_snaptrade_order_symbol_accepts_both_shapes_and_rejects_neither():
    from sgt.brokers.snaptrade_mapping import order_symbol
    assert order_symbol({"universal_symbol": {"symbol": "AAA"}}) == "AAA"
    assert order_symbol({"symbol": {"symbol": {"symbol": "BBB"}}}) == "BBB"
    assert order_symbol({"status": "ACCEPTED"}) == ""  # neither shape -> no match


async def test_verify_snaptrade_cancel_keeps_working_until_confirmed_terminal(monkeypatch):
    import sgt.brokers.snaptrade as st
    monkeypatch.setattr(st, "_CANCEL_POLL_INTERVAL_S", 0.0)
    working = [snaptrade_order_record("boid-1", symbol="AAA", action="BUY", qty=10,
                                      status="ACCEPTED")]
    with respx.mock(base_url=BASE_ST, assert_all_called=False) as r:
        _register_base(r, orders_box=working)
        r.post("/api/v1/trade/place").mock(return_value=httpx.Response(
            200, json=snaptrade_order_record("boid-1", symbol="AAA", action="BUY", qty=10,
                                             status="ACCEPTED")))
        r.post(f"/api/v1/accounts/{ST_ACCT}/trading/cancel").mock(
            return_value=httpx.Response(200, json={"brokerage_order_id": "boid-1"}))
        broker = SnapTradeBroker("acct", ST_ACCT, trade_enabled=True,
                                 authorization_id=AUTH_ID, client=_st_client())
        await broker.connect()
        await broker.submit(make_intent(cid="cx-1", limit_price=5.5))
        ack = await broker.cancel("cx-1")
        # Order never observed terminal -> ledger stays working, ack is not CANCELED.
        assert ack.status is OrderStatus.WORKING
        with get_session() as s:
            assert s.get(OrderLedger, "cx-1").status != "canceled"
        await broker.close()
