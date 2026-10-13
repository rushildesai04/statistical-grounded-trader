"""Shared fixtures for broker adapter tests.

Provides a per-adapter *harness* so the contract suite (test_contract.py) can exercise
the common async BrokerAdapter surface across SimBroker, AlpacaBroker (respx-mocked
paper API) and AlertOnlyBroker with one parametrized body.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

# Alpaca credentials for tests (keychain is the null backend via the root conftest).
os.environ.setdefault("SGT_SECRET_ALPACA_KEY_ID", "test-key-id")
os.environ.setdefault("SGT_SECRET_ALPACA_SECRET_KEY", "test-secret-key")

import httpx  # noqa: E402
import pytest  # noqa: E402
import respx  # noqa: E402

from sgt.brokers import AlertOnlyBroker, AlpacaBroker, CostModel, LogNotifier, SimBroker  # noqa: E402
from sgt.core.types import OrderType  # noqa: E402

from brk_helpers import make_bar, make_intent  # noqa: E402

UTC = timezone.utc


# --------------------------------------------------------------------------- harnesses
class SimHarness:
    name = "sim"

    def __init__(self):
        self.adapter = SimBroker("acct", CostModel(), 1_000_000.0)

    async def setup(self):
        await self.adapter.connect()

    async def teardown(self):
        await self.adapter.close()

    def intent(self, cid="cid-1"):
        return make_intent(cid=cid, order_type=OrderType.MARKET, limit_price=None)

    async def arrange_one_fill(self):
        intent = self.intent("fill-cid")
        await self.adapter.submit(intent)
        self.adapter.on_bar(make_bar())  # eligible this bar, fills fully
        return intent

    async def arrange_open_order(self):
        # A LIMIT buy that stays working (no bar processed) -> an open order.
        await self.adapter.submit(make_intent(cid="open-cid", limit_price=5.5))


class AlertHarness:
    name = "alert_only"

    def __init__(self):
        self.adapter = AlertOnlyBroker("acct", LogNotifier())

    async def setup(self):
        await self.adapter.connect()

    async def teardown(self):
        await self.adapter.close()

    def intent(self, cid="cid-1"):
        return make_intent(cid=cid)

    async def arrange_one_fill(self):
        intent = self.intent("fill-cid")
        await self.adapter.submit(intent)
        self.adapter.record_manual_fill(
            intent.client_order_id, intent.symbol, intent.side, intent.qty, 5.4,
            datetime(2026, 10, 12, 14, 35, tzinfo=UTC))
        return intent

    async def arrange_open_order(self):
        await self.adapter.submit(make_intent(cid="open-cid"))  # PENDING == open


def _order_json(cid, *, status="new", oid="brk-1"):
    return {"id": oid, "client_order_id": cid, "status": status,
            "submitted_at": "2026-10-12T14:31:00Z", "symbol": "AAA",
            "qty": "10", "side": "buy"}


def _activity_json(cid, *, aid="act-1", oid="brk-1"):
    return {"id": aid, "activity_type": "FILL", "transaction_time": "2026-10-12T14:35:00Z",
            "symbol": "AAA", "side": "buy", "qty": "10", "price": "5.00",
            "order_id": oid, "client_order_id": cid}


class AlpacaHarness:
    name = "alpaca"
    base = "https://paper-api.alpaca.markets"

    def __init__(self):
        self.adapter = AlpacaBroker("acct", paper=True)
        self.router = respx.mock(base_url=self.base, assert_all_called=False)
        # cid -> broker order id; each POST mints a NEW id so a double submit is
        # observable as a broker_order_id mismatch in the contract suite (review T1).
        self._posted: dict[str, str] = {}
        self.post_count = 0

    async def setup(self):
        r = self.router
        r.start()
        r.get("/v2/account").mock(return_value=httpx.Response(
            200, json={"cash": "1000", "buying_power": "1000",
                       "non_marginable_buying_power": "1000", "status": "ACTIVE"}))
        r.get("/v2/positions").mock(return_value=httpx.Response(200, json=[]))
        r.get("/v2/orders").mock(return_value=httpx.Response(200, json=[]))

        def lookup(request):
            cid = request.url.params.get("client_order_id")
            if cid in self._posted:
                return httpx.Response(200, json=_order_json(cid, oid=self._posted[cid]))
            return httpx.Response(404, json={"message": "order not found"})
        r.get("/v2/orders:by_client_order_id").mock(side_effect=lookup)

        def post(request):
            import json as _json
            cid = _json.loads(request.content)["client_order_id"]
            self.post_count += 1
            self._posted[cid] = f"brk-{self.post_count}"
            return httpx.Response(201, json=_order_json(cid, oid=self._posted[cid]))
        r.post("/v2/orders").mock(side_effect=post)

        r.delete(url__regex=r"/v2/orders/.+").mock(return_value=httpx.Response(204))
        await self.adapter.connect()

    async def teardown(self):
        await self.adapter.close()
        self.router.stop()

    def intent(self, cid="cid-1"):
        return make_intent(cid=cid)

    async def arrange_one_fill(self):
        intent = self.intent("fill-cid")
        await self.adapter.submit(intent)
        self.router.get("/v2/account/activities/FILL").mock(
            return_value=httpx.Response(200, json=[_activity_json(intent.client_order_id)]))
        return intent

    async def arrange_open_order(self):
        self.router.get("/v2/orders").mock(
            return_value=httpx.Response(200, json=[_order_json("open-cid")]))


_HARNESSES = {"sim": SimHarness, "alert_only": AlertHarness, "alpaca": AlpacaHarness}


@pytest.fixture(params=list(_HARNESSES))
async def harness(request):
    h = _HARNESSES[request.param]()
    await h.setup()
    try:
        yield h
    finally:
        await h.teardown()
