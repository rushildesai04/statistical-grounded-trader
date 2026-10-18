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
from conftest import _schwab_order, snaptrade_order_record
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


# =========================================================================== PASS-2 (R2-*)
# 2026-10-18 fresh connector review. Mocks honour the query params the adapter sends
# (SnapTrade `days`, Schwab single-order reads) so a too-narrow window is caught.

def _honour_days(orders: list[dict]):
    """GET /orders handler that only returns orders placed within `days` of real now."""
    def handler(request):
        days = int(request.url.params.get("days", "30"))
        floor = datetime.now(UTC) - timedelta(days=days)
        return httpx.Response(200, json=[
            o for o in orders if smap.parse_ts(o["time_placed"]) >= floor])
    return handler


def _st_broker():
    return SnapTradeBroker("acct", ST_ACCT, trade_enabled=True, authorization_id=AUTH_ID,
                           client=_st_client())


def _seed_pending(cid: str, created: datetime):
    seed = make_intent(cid=cid, limit_price=5.5)
    object.__setattr__(seed, "created_at", created)
    with get_session() as s:
        record_ack(s, OrderAck(cid, "", OrderStatus.PENDING, created, "pre",
                               symbol="AAA", side=Side.BUY, remaining_qty=10), seed)


async def test_r2a_snaptrade_restart_days_later_reconciles_not_duplicates():
    """Pre-fix: reconcile queried days=1, missed a 3-day-old landed order, and POSTed again."""
    created = datetime.now(UTC) - timedelta(days=3)
    landed = snaptrade_order_record("boid-old", symbol="AAA", action="BUY", qty=10,
                                    status="ACCEPTED", limit_price=5.5, time_placed=created)
    posts = {"n": 0}
    with respx.mock(base_url=BASE_ST, assert_all_called=False) as r:
        _register_base(r)
        r.get(f"/api/v1/accounts/{ST_ACCT}/orders").mock(side_effect=_honour_days([landed]))

        def place(request):
            posts["n"] += 1
            return httpx.Response(200, json=snaptrade_order_record("boid-dupe"))
        r.post("/api/v1/trade/place").mock(side_effect=place)
        broker = _st_broker()
        await broker.connect()
        _seed_pending("r2a-1", created)
        intent = make_intent(cid="r2a-1", limit_price=5.5)
        object.__setattr__(intent, "created_at", created)
        ack = await broker.submit(intent)
        assert ack.broker_order_id == "boid-old"
        assert posts["n"] == 0
        await broker.close()


async def test_r2a_snaptrade_refuses_when_anchor_beyond_lookback():
    created = datetime.now(UTC) - timedelta(days=12)
    posts = {"n": 0}
    with respx.mock(base_url=BASE_ST, assert_all_called=False) as r:
        _register_base(r)

        def place(request):
            posts["n"] += 1
            return httpx.Response(200, json=snaptrade_order_record("boid-dupe"))
        r.post("/api/v1/trade/place").mock(side_effect=place)
        broker = _st_broker()
        await broker.connect()
        _seed_pending("r2a-2", created)
        intent = make_intent(cid="r2a-2", limit_price=5.5)
        object.__setattr__(intent, "created_at", created)
        with pytest.raises(BrokerUnavailableError, match="older than"):
            await broker.submit(intent)
        assert posts["n"] == 0
        with get_session() as s:
            assert s.get(OrderLedger, "r2a-2").status == "unknown"
        await broker.close()


async def _drain(broker):
    return [f async for f in broker.fills()]


async def test_r2b_snaptrade_restart_emits_only_the_fill_delta():
    """Pre-fix: the cumulative baseline lived in memory, so after a restart the whole
    cumulative quantity was re-emitted under a new fill_id (double-counted position)."""
    now = datetime.now(UTC)
    rec = snaptrade_order_record("boid-pf", symbol="AAA", action="BUY", qty=10,
                                 status="PARTIAL", filled=4, execution_price=5.0,
                                 limit_price=5.5, time_placed=now)
    box = [rec]
    with respx.mock(base_url=BASE_ST, assert_all_called=False) as r:
        _register_base(r, orders_box=box)
        r.post("/api/v1/trade/place").mock(return_value=httpx.Response(200, json=rec))
        b1 = _st_broker()
        await b1.connect()
        await b1.submit(make_intent(cid="r2b-1", limit_price=5.5))
        first = await _drain(b1)
        assert [f.qty for f in first] == [4]
        await b1.close()

        # restart: fresh adapter, no in-memory tracking; order now fully filled
        box[0] = snaptrade_order_record("boid-pf", symbol="AAA", action="BUY", qty=10,
                                        status="EXECUTED", filled=10, execution_price=5.0,
                                        limit_price=5.5, time_placed=now)
        b2 = _st_broker()
        await b2.connect()
        second = await _drain(b2)
        assert [(f.qty, f.client_order_id) for f in second] == [(6, "r2b-1")]
        assert await _drain(b2) == []
        await b2.close()

        # another restart: everything already persisted -> nothing re-yielded
        b3 = _st_broker()
        await b3.connect()
        assert await _drain(b3) == []
        await b3.close()


async def test_r2c_schwab_cancel_race_with_fill_records_filled(monkeypatch):
    """Pre-fix: DELETE 400 (order already terminal) was recorded as CANCELED even when
    the order had FILLED, erasing a real position from the ledger."""
    import sgt.brokers.schwab as sch
    monkeypatch.setattr(sch, "_CANCEL_POLL_INTERVAL_S", 0.0)
    store = _FakeSecrets()
    with respx.mock(base_url=SCHWAB_BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.post("/trader/v1/accounts/HASH/orders").mock(return_value=httpx.Response(
            201, headers={"Location": f"{SCHWAB_BASE}/trader/v1/accounts/HASH/orders/77"}))
        r.delete("/trader/v1/accounts/HASH/orders/77").mock(return_value=httpx.Response(400))
        r.get("/trader/v1/accounts/HASH/orders/77").mock(return_value=httpx.Response(
            200, json=_schwab_order("77", status="FILLED", filled=10)))
        broker = _make_broker(store)
        await broker.submit(make_intent(cid="r2c-1", limit_price=5.5))
        ack = await broker.cancel("r2c-1")
        assert ack.status is OrderStatus.FILLED
        with get_session() as s:
            assert s.get(OrderLedger, "r2c-1").status == "filled"
        await broker.close()


async def test_r2c_schwab_cancel_unconfirmed_stays_working(monkeypatch):
    import sgt.brokers.schwab as sch
    monkeypatch.setattr(sch, "_CANCEL_POLL_INTERVAL_S", 0.0)
    store = _FakeSecrets()
    with respx.mock(base_url=SCHWAB_BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.post("/trader/v1/accounts/HASH/orders").mock(return_value=httpx.Response(
            201, headers={"Location": f"{SCHWAB_BASE}/trader/v1/accounts/HASH/orders/78"}))
        r.delete("/trader/v1/accounts/HASH/orders/78").mock(return_value=httpx.Response(200))
        r.get("/trader/v1/accounts/HASH/orders/78").mock(return_value=httpx.Response(
            200, json=_schwab_order("78", status="PENDING_CANCEL")))
        broker = _make_broker(store)
        await broker.submit(make_intent(cid="r2c-2", limit_price=5.5))
        ack = await broker.cancel("r2c-2")
        assert ack.status is OrderStatus.WORKING
        with get_session() as s:
            assert s.get(OrderLedger, "r2c-2").status not in ("canceled", "filled")
        await broker.close()


def test_r2c_schwab_ledger_never_regresses_terminal():
    from sgt.brokers.schwab_ledger import ledger_mark_from_ack
    intent = make_intent(cid="r2c-3")
    now = datetime.now(UTC)
    ledger_mark(intent, "filled", "B1", now)
    ledger_mark_from_ack("r2c-3", OrderAck("r2c-3", "B1", OrderStatus.CANCELED, now, "late"), now)
    ledger_mark_from_ack("r2c-3", OrderAck("r2c-3", "B1", OrderStatus.WORKING, now, "late"), now)
    with get_session() as s:
        assert s.get(OrderLedger, "r2c-3").status == "filled"


async def test_r2d_snaptrade_concurrent_same_cid_posts_once():
    """Pre-fix: the second coroutine saw the first's PENDING row (no broker id yet),
    reconciled to zero matches and POSTed a duplicate."""
    posts = {"n": 0}
    with respx.mock(base_url=BASE_ST, assert_all_called=False) as r:
        _register_base(r)

        async def place(request):
            posts["n"] += 1
            await asyncio.sleep(0.01)          # POST in flight while the twin arrives
            return httpx.Response(200, json=snaptrade_order_record(
                f"boid-{posts['n']}", symbol="AAA", action="BUY", qty=10, status="ACCEPTED"))
        r.post("/api/v1/trade/place").mock(side_effect=place)
        broker = _st_broker()
        await broker.connect()
        a, b = await asyncio.gather(broker.submit(make_intent(cid="r2d-1", limit_price=5.5)),
                                    broker.submit(make_intent(cid="r2d-1", limit_price=5.5)))
        assert posts["n"] == 1
        assert a.broker_order_id == b.broker_order_id == "boid-1"
        assert len(broker._submit_locks) == 0          # locks are released, not leaked
        await broker.close()


async def test_r2d_schwab_lost_claim_race_takes_existing_row_path(monkeypatch):
    """Another process inserts the ledger row between our lookup and our insert: the
    atomic claim fails and we adopt its order instead of POSTing a second one."""
    import sgt.brokers.schwab_ledger as sl
    store = _FakeSecrets()
    real_lookup = sl.ledger_lookup
    calls = {"n": 0}

    def stale_then_real(cid):
        calls["n"] += 1
        if calls["n"] == 1:
            ledger_mark(make_intent(cid="r2d-2"), "working", "OTHER-1", datetime.now(UTC))
            return None                 # our read happened just before the other insert
        return real_lookup(cid)
    monkeypatch.setattr(sl, "ledger_lookup", stale_then_real)
    posts = {"n": 0}
    with respx.mock(base_url=SCHWAB_BASE, assert_all_called=False) as r:
        _token_ok(r)

        def post(request):
            posts["n"] += 1
            return httpx.Response(201, headers={
                "Location": f"{SCHWAB_BASE}/trader/v1/accounts/HASH/orders/9"})
        r.post("/trader/v1/accounts/HASH/orders").mock(side_effect=post)
        broker = _make_broker(store)
        ack = await broker.submit(make_intent(cid="r2d-2", limit_price=5.5))
        assert ack.broker_order_id == "OTHER-1"
        assert posts["n"] == 0
        await broker.close()


def test_r2d_claim_ledger_row_is_exclusive():
    from sgt.brokers.submit_guard import claim_ledger_row
    intent = make_intent(cid="r2d-3")
    now = datetime.now(UTC)
    assert claim_ledger_row(intent, "submitting", smap.intent_to_json(intent), now) is True
    assert claim_ledger_row(intent, "submitting", smap.intent_to_json(intent), now) is False
