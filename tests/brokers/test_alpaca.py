"""AlpacaBroker: idempotency, error mapping, safe POST retry, activity pagination."""
from __future__ import annotations

import httpx
import pytest
import respx

from sgt.brokers import AlpacaBroker
from sgt.core.interfaces import (
    BrokerAuthError,
    BrokerRejectedError,
    BrokerUnavailableError,
)

from brk_helpers import make_intent

BASE = "https://paper-api.alpaca.markets"


def _order(cid, status="new", oid="brk-1"):
    return {"id": oid, "client_order_id": cid, "status": status,
            "submitted_at": "2026-10-12T14:31:00Z", "symbol": "AAA", "qty": "10", "side": "buy"}


def _act(aid, oid, cid, qty="10", price="5.00"):
    return {"id": aid, "activity_type": "FILL", "transaction_time": "2026-10-12T14:35:00Z",
            "symbol": "AAA", "side": "buy", "qty": qty, "price": price,
            "order_id": oid, "client_order_id": cid}


async def test_401_maps_to_auth_error():
    with respx.mock(base_url=BASE) as r:
        r.get("/v2/account").mock(return_value=httpx.Response(401, json={"message": "unauthorized"}))
        broker = AlpacaBroker("acct", paper=True)
        with pytest.raises(BrokerAuthError):
            await broker.connect()
        await broker.close()


async def test_422_rejected_maps_to_rejected_error():
    with respx.mock(base_url=BASE) as r:
        r.get("/v2/orders:by_client_order_id").mock(return_value=httpx.Response(404))
        r.post("/v2/orders").mock(return_value=httpx.Response(
            422, json={"message": "qty must be positive"}))
        broker = AlpacaBroker("acct", paper=True)
        with pytest.raises(BrokerRejectedError):
            await broker.submit(make_intent(cid="rej"))
        await broker.close()


async def test_duplicate_client_id_returns_existing():
    calls = {"lookup": 0}

    def lookup(request):
        calls["lookup"] += 1
        if calls["lookup"] == 1:
            return httpx.Response(404)
        return httpx.Response(200, json=_order("dup", status="new", oid="brk-9"))

    with respx.mock(base_url=BASE) as r:
        r.get("/v2/orders:by_client_order_id").mock(side_effect=lookup)
        r.post("/v2/orders").mock(return_value=httpx.Response(
            422, json={"message": "client_order_id must be unique"}))
        broker = AlpacaBroker("acct", paper=True)
        ack = await broker.submit(make_intent(cid="dup"))
        assert ack.broker_order_id == "brk-9"
        assert calls["lookup"] == 2  # pre-POST miss, then post-422 hit
        await broker.close()


async def test_post_timeout_confirms_via_lookup_no_double_submit():
    posts = {"n": 0}

    def post(request):
        posts["n"] += 1
        raise httpx.ConnectTimeout("boom")

    def lookup(request):
        # Pre-POST: not found. Post-timeout: order actually landed.
        return httpx.Response(404) if posts["n"] == 0 else httpx.Response(
            200, json=_order("to", oid="brk-7"))

    with respx.mock(base_url=BASE) as r:
        r.get("/v2/orders:by_client_order_id").mock(side_effect=lookup)
        r.post("/v2/orders").mock(side_effect=post)
        broker = AlpacaBroker("acct", paper=True)
        ack = await broker.submit(make_intent(cid="to"))
        assert ack.broker_order_id == "brk-7"
        assert posts["n"] == 1  # POST attempted once; lookup confirmed, no retry
        await broker.close()


async def test_5xx_retries_then_unavailable():
    with respx.mock(base_url=BASE) as r:
        r.get("/v2/account").mock(return_value=httpx.Response(503))
        broker = AlpacaBroker("acct", paper=True)
        with pytest.raises(BrokerUnavailableError):
            await broker.account_state()
        await broker.close()


async def test_reject_market_in_extended_hours():
    # OrderIntent forbids ext-hours non-LIMIT at construction; bypass that to prove the
    # adapter's own defensive guard still rejects a MARKET order flagged extended_hours.
    from sgt.core.types import OrderType
    intent = make_intent(cid="m", order_type=OrderType.MARKET, limit_price=None)
    object.__setattr__(intent, "extended_hours", True)
    with respx.mock(base_url=BASE):
        broker = AlpacaBroker("acct", paper=True)
        with pytest.raises(BrokerRejectedError):
            await broker.submit(intent)
        await broker.close()


async def test_fills_pagination_and_dedupe():
    def activities(request):
        after = request.url.params.get("after")
        if after is None:
            return httpx.Response(200, json=[_act("a1", "o1", "c1"), _act("a2", "o2", "c2")])
        if after == "a2":
            return httpx.Response(200, json=[_act("a3", "o3", "c3")])
        return httpx.Response(200, json=[])

    with respx.mock(base_url=BASE) as r:
        r.get("/v2/account/activities/FILL").mock(side_effect=activities)
        broker = AlpacaBroker("acct", paper=True)
        page1 = [f async for f in broker.fills()]
        assert [f.fill_id for f in page1] == ["a1", "a2"]
        page2 = [f async for f in broker.fills()]
        assert [f.fill_id for f in page2] == ["a3"]  # cursor advanced, no dupes
        page3 = [f async for f in broker.fills()]
        assert page3 == []
        await broker.close()


LIVE_BASE = "https://api.alpaca.markets"


@pytest.mark.parametrize("shorting", [True, None])
async def test_live_connect_refuses_shorting_capable_or_unknown_account(shorting, monkeypatch):
    """PASS 3 F-3: a long-only system must not trade live on an account that can short."""
    monkeypatch.setenv("SGT_SECRET_ALPACA_LIVE_KEY_ID", "k")
    monkeypatch.setenv("SGT_SECRET_ALPACA_LIVE_SECRET_KEY", "s")
    acct = {"cash": "1000", "buying_power": "1000", "status": "ACTIVE"}
    if shorting is not None:
        acct["shorting_enabled"] = shorting
    with respx.mock(base_url=LIVE_BASE) as r:
        r.get("/v2/account").mock(return_value=httpx.Response(200, json=acct))
        broker = AlpacaBroker("live-acct", paper=False)
        with pytest.raises(BrokerRejectedError):
            await broker.connect()
        await broker.close()


async def test_live_connect_accepts_long_only_account(monkeypatch):
    monkeypatch.setenv("SGT_SECRET_ALPACA_LIVE_KEY_ID", "k")
    monkeypatch.setenv("SGT_SECRET_ALPACA_LIVE_SECRET_KEY", "s")
    acct = {"cash": "1000", "buying_power": "1000", "status": "ACTIVE", "shorting_enabled": False}
    with respx.mock(base_url=LIVE_BASE) as r:
        r.get("/v2/account").mock(return_value=httpx.Response(200, json=acct))
        broker = AlpacaBroker("live-acct", paper=False)
        await broker.connect()
        await broker.close()
