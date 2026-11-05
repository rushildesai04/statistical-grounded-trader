"""Live pilot shorts at Schwab (2026-11-05, red-team F-4/F-8): signed positions, SELL_SHORT / BUY_TO_COVER
instructions, the owner's long shares never shorted, covers capped at the broker short minus working covers."""
from __future__ import annotations

import dataclasses
import json

import httpx
import pytest
import respx

from sgt.brokers import schwab_mapping as smap
from sgt.core.interfaces import BrokerRejectedError
from sgt.core.types import OrderType, Side

from brk_helpers import make_intent
from test_schwab import BASE, _FakeSecrets, _make_broker, _token_ok


def _short(**kw):
    return dataclasses.replace(make_intent(**kw), short=True)


def _account(*rows):
    return {"securitiesAccount": {"currentBalances": {"cashBalance": 0.0, "buyingPower": 0.0}, "positions": [
        {"instrument": {"symbol": s}, "longQuantity": lq, "shortQuantity": sq, "averagePrice": 4.0}
        for s, lq, sq in rows]}}


def _cover_order(qty, status="WORKING", instruction="BUY_TO_COVER"):
    return {"orderId": "stop-1", "status": status, "enteredTime": "2026-10-18T13:31:00.000Z", "filledQuantity": 0,
            "remainingQuantity": qty, "orderLegCollection": [{"instruction": instruction, "quantity": qty,
                                                              "instrument": {"symbol": "AAA"}}]}


def test_signed_positions_and_instructions():
    assert smap.signed_positions(_account(("AAA", 0, 300), ("SPY", 5, 0), ("ZZZ", 0, 0))) == {"AAA": -300, "SPY": 5}
    with pytest.raises(ValueError):
        smap.signed_positions(_account(("AAA", 0, float("nan"))))
    assert smap.instruction(_short(side=Side.SELL)) == "SELL_SHORT"
    assert smap.instruction(_short(side=Side.BUY)) == "BUY_TO_COVER"
    assert smap.instruction(make_intent(side=Side.SELL)) == "SELL"          # existing paths unchanged
    body = smap.order_body(_short(side=Side.SELL), "NORMAL")
    assert body["orderLegCollection"][0]["instruction"] == "SELL_SHORT"
    assert "short" not in smap.intent_to_json(make_intent())               # stored intents of old paths unchanged
    assert smap.order_matches({"orderLegCollection": [{"instruction": "SELL_SHORT", "quantity": 10,
                                                       "instrument": {"symbol": "AAA"}}], "price": 5.5},
                              _short(side=Side.SELL))


def _run(account, orders, intent):
    posts: list[dict] = []

    async def go():
        with respx.mock(base_url=BASE, assert_all_called=False) as r:
            _token_ok(r)
            r.get("/trader/v1/accounts/HASH").mock(return_value=httpx.Response(200, json=account))
            r.get("/trader/v1/accounts/HASH/orders").mock(return_value=httpx.Response(200, json=orders))
            r.post("/trader/v1/accounts/HASH/orders").mock(side_effect=lambda req: (
                posts.append(json.loads(req.content)),
                httpx.Response(201, headers={"Location": f"{BASE}/trader/v1/accounts/HASH/orders/77"}))[1])
            broker = _make_broker(_FakeSecrets())
            try:
                return await broker.submit(intent)
            finally:
                await broker.close()
    return go, posts


async def test_sell_short_posts_short_instruction():
    go, posts = _run(_account(), [], _short(cid="s1", side=Side.SELL, qty=50))
    ack = await go()
    assert ack.broker_order_id == "77"
    assert posts[0]["orderLegCollection"][0]["instruction"] == "SELL_SHORT"


async def test_sell_short_refused_on_owner_long_shares():
    go, posts = _run(_account(("AAA", 10, 0)), [], _short(cid="s2", side=Side.SELL, qty=50))
    with pytest.raises(BrokerRejectedError, match="holds 10 long"):
        await go()
    assert posts == []


async def test_sell_short_must_be_limit():
    go, posts = _run(_account(), [], _short(cid="s3", side=Side.SELL, order_type=OrderType.MARKET,
                                             limit_price=None))
    with pytest.raises(BrokerRejectedError, match="limit"):
        await go()
    assert posts == []


@pytest.mark.parametrize("working, qty, ok", [(0, 100, True), (0, 101, False), (100, 1, False), (40, 60, True)])
async def test_cover_capped_at_broker_short_minus_working_covers(working, qty, ok):
    orders = [_cover_order(working)] if working else []
    orders.append(_cover_order(500, instruction="BUY"))                   # a plain BUY never counts as a cover
    orders.append(_cover_order(500, status="FILLED"))                     # nor does a finished cover
    go, posts = _run(_account(("AAA", 0, 100)), orders, _short(cid=f"c{working}{qty}", side=Side.BUY, qty=qty))
    if ok:
        await go()
        assert posts[0]["orderLegCollection"][0]["instruction"] == "BUY_TO_COVER"
    else:
        with pytest.raises(BrokerRejectedError, match="cover cap"):
            await go()
        assert posts == []


async def test_cover_refused_when_no_broker_short():
    go, posts = _run(_account(("AAA", 10, 0)), [], _short(cid="c-none", side=Side.BUY, qty=1))
    with pytest.raises(BrokerRejectedError, match="cover cap"):
        await go()
    assert posts == []
