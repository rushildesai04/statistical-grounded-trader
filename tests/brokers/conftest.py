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

from sgt.brokers import (  # noqa: E402
    AlertOnlyBroker,
    AlpacaBroker,
    CostModel,
    LogNotifier,
    SchwabBroker,
    SchwabTokenManager,
    SimBroker,
    SnapTradeBroker,
    SnapTradeClient,
)
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


class IBKRHarness:
    name = "ibkr"

    def __init__(self):
        # Imported lazily so the fake (and ib_async-free test run) is only pulled in at
        # fixture setup, never at collection time.
        from sgt.brokers.ibkr import IBKRBroker
        from test_ibkr import FakeIB

        self.ib = FakeIB()
        self.adapter = IBKRBroker("acct", "DU111", paper=True, ib=self.ib)

    async def setup(self):
        await self.adapter.connect()

    async def teardown(self):
        await self.adapter.close()

    def intent(self, cid="cid-1"):
        return make_intent(cid=cid)

    async def arrange_one_fill(self):
        intent = self.intent("fill-cid")
        await self.adapter.submit(intent)
        self.ib.add_execution(cid=intent.client_order_id, exec_id="harness-exec-1")
        return intent

    async def arrange_open_order(self):
        await self.adapter.submit(make_intent(cid="open-cid"))  # LIMIT stays working


class _FakeSecrets:
    """In-memory secret store so the token manager can persist without a keychain."""

    def __init__(self, initial):
        self._d = dict(initial)

    def get(self, name, required=True):
        return self._d.get(name)

    def set(self, name, value):
        self._d[name] = value


def _schwab_order(oid, *, status="WORKING", symbol="AAA", instruction="BUY", qty=10,
                  filled=0, exec_legs=None):
    order = {
        "orderId": oid,
        "status": status,
        "enteredTime": "2026-10-12T14:31:00.000Z",
        "filledQuantity": filled,
        "remainingQuantity": qty - filled,
        "orderLegCollection": [{
            "instruction": instruction, "quantity": qty,
            "instrument": {"symbol": symbol, "assetType": "EQUITY"}}],
    }
    if exec_legs is not None:
        order["orderActivityCollection"] = [{"executionLegs": exec_legs}]
    return order


class SchwabHarness:
    name = "schwab"
    base = "https://api.schwabapi.com"

    def __init__(self):
        from datetime import datetime as _dt
        store = _FakeSecrets({
            "schwab_app_key": "app-key",
            "schwab_app_secret": "app-secret",
            "schwab_refresh_token": "rt-0",
            "schwab_refresh_issued_at": _dt(2026, 10, 12, 15, 0, tzinfo=UTC).isoformat(),
        })
        fixed_now = lambda: _dt(2026, 10, 12, 15, 0, tzinfo=UTC)  # noqa: E731 - wall-clock independent
        auth = SchwabTokenManager(secret_get=store.get, secret_set=store.set, clock=fixed_now)
        self.adapter = SchwabBroker("acct", "5678", auth=auth, clock=fixed_now)
        self.router = respx.mock(base_url=self.base, assert_all_called=False)
        self.orders: list = []
        self.post_count = 0
        self.last_order_id = ""
        self.canceled: set[str] = set()

    async def setup(self):
        r = self.router
        r.start()
        r.post("/v1/oauth/token").mock(return_value=httpx.Response(
            200, json={"access_token": "at-1", "refresh_token": "rt-1",
                       "expires_in": 1800, "token_type": "Bearer"}))
        r.get("/trader/v1/accounts/accountNumbers").mock(return_value=httpx.Response(
            200, json=[{"accountNumber": "12345678", "hashValue": "HASH"}]))
        r.get("/trader/v1/accounts/HASH").mock(return_value=httpx.Response(
            200, json={"securitiesAccount": {
                "currentBalances": {"cashBalance": 1000.0, "buyingPower": 2000.0,
                                    "cashAvailableForTrading": 1000.0},
                "positions": []}}))
        r.get("/trader/v1/accounts/HASH/orders").mock(
            side_effect=lambda request: httpx.Response(200, json=self.orders))

        def post(request):
            self.post_count += 1
            self.last_order_id = f"{1000 + self.post_count}"
            return httpx.Response(201, headers={
                "Location": f"{self.base}/trader/v1/accounts/HASH/orders/{self.last_order_id}"})
        r.post("/trader/v1/accounts/HASH/orders").mock(side_effect=post)
        # Stateful cancel: DELETE accepts; the single-order read-back (R2-C) then reports
        # CANCELED, as Schwab does once the cancel has been processed.
        def delete(request):
            self.canceled.add(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(200)

        def get_one(request):
            oid = request.url.path.rsplit("/", 1)[-1]
            status = "CANCELED" if oid in self.canceled else "WORKING"
            return httpx.Response(200, json=_schwab_order(oid, status=status))
        r.delete(url__regex=r"/trader/v1/accounts/HASH/orders/.+").mock(side_effect=delete)
        r.get(url__regex=r"/trader/v1/accounts/HASH/orders/.+").mock(side_effect=get_one)
        await self.adapter.connect()

    async def teardown(self):
        await self.adapter.close()
        self.router.stop()

    def intent(self, cid="cid-1"):
        return make_intent(cid=cid)

    async def arrange_one_fill(self):
        intent = self.intent("fill-cid")
        await self.adapter.submit(intent)
        self.orders = [_schwab_order(
            self.last_order_id, status="FILLED",
            exec_legs=[{"legId": 1, "price": 5.0, "quantity": 10,
                        "time": "2026-10-12T14:35:00.000Z"}])]
        return intent

    async def arrange_open_order(self):
        self.orders = [_schwab_order("open-1", status="WORKING", qty=10, filled=0)]


def snaptrade_order_record(boid, *, symbol="AAA", action="BUY", qty=10, status="ACCEPTED",
                           filled=0, canceled=0, open_q=None, execution_price=None,
                           limit_price=None, time_placed=None):
    """Build an AccountOrderRecord JSON payload (quantities are strings, per SnapTrade)."""
    rec = {
        "brokerage_order_id": boid,
        "status": status,
        "action": action,
        "total_quantity": str(qty),
        "filled_quantity": str(filled),
        "canceled_quantity": str(canceled),
        "open_quantity": str(open_q if open_q is not None else qty - filled - canceled),
        "order_type": "Limit",
        "time_in_force": "Day",
        "time_placed": (time_placed or datetime(2026, 10, 12, 14, 30, tzinfo=UTC)).isoformat(),
        "universal_symbol": {"id": f"us-{symbol}", "symbol": symbol},
    }
    if execution_price is not None:
        rec["execution_price"] = str(execution_price)
    if limit_price is not None:
        rec["limit_price"] = str(limit_price)
    return rec


class SnapTradeHarness:
    name = "snaptrade"
    base = "https://api.snaptrade.com"
    ST_ACCT = "st-acct-1"
    AUTH_ID = "auth-1"

    def __init__(self):
        client = SnapTradeClient(
            client_id="test-client", consumer_key="test-consumer-key",
            user_id="user-123", user_secret="sec-xyz",
            trade_min_interval_s=0.0)  # no spacing delay in the contract suite
        self.adapter = SnapTradeBroker("acct", self.ST_ACCT, trade_enabled=True,
                                       authorization_id=self.AUTH_ID, client=client)
        self.router = respx.mock(base_url=self.base, assert_all_called=False)
        self.post_count = 0
        self._orders: list = []

    async def setup(self):
        r = self.router
        r.start()
        r.get("/api/v1/").mock(return_value=httpx.Response(200, json={"online": True}))
        r.get(f"/api/v1/authorizations/{self.AUTH_ID}").mock(return_value=httpx.Response(
            200, json={"id": self.AUTH_ID, "type": "trade", "disabled": False}))
        r.get("/api/v1/accounts/all").mock(return_value=httpx.Response(200, json=[
            {"id": self.ST_ACCT, "brokerage_authorization": self.AUTH_ID,
             "institution_name": "TestBroker", "number": "X1", "is_paper": False}]))
        r.get(f"/api/v1/accounts/{self.ST_ACCT}/balances").mock(return_value=httpx.Response(
            200, json=[{"currency": {"code": "USD"}, "cash": 1000.0, "buying_power": 1000.0}]))
        r.get(f"/api/v1/accounts/{self.ST_ACCT}/positions/all").mock(
            return_value=httpx.Response(200, json=[]))
        r.get(f"/api/v1/accounts/{self.ST_ACCT}/orders").mock(
            side_effect=lambda request: httpx.Response(200, json=self._orders))

        def place(request):
            import json as _json
            body = _json.loads(request.content)
            self.post_count += 1
            return httpx.Response(200, json=snaptrade_order_record(
                f"boid-{self.post_count}", symbol=body["symbol"], action=body["action"],
                qty=int(body["units"]), status="ACCEPTED", limit_price=body.get("price")))
        r.post("/api/v1/trade/place").mock(side_effect=place)

        def cancel(request):
            import json as _json
            boid = _json.loads(request.content).get("brokerage_order_id", "boid-1")
            # The brokerage cancels asynchronously; the adapter polls order status before
            # marking the ledger terminal, so reflect the order as CANCELED in the list.
            self._orders = [snaptrade_order_record(boid, symbol="AAA", action="BUY", qty=10,
                                                   status="CANCELED", canceled=10, open_q=0)]
            return httpx.Response(200, json={"brokerage_order_id": boid})
        r.post(f"/api/v1/accounts/{self.ST_ACCT}/trading/cancel").mock(side_effect=cancel)
        await self.adapter.connect()

    async def teardown(self):
        await self.adapter.close()
        self.router.stop()

    def intent(self, cid="cid-1"):
        return make_intent(cid=cid)

    async def arrange_one_fill(self):
        intent = self.intent("fill-cid")
        ack = await self.adapter.submit(intent)
        self._orders = [snaptrade_order_record(
            ack.broker_order_id, symbol="AAA", action="BUY", qty=10, status="EXECUTED",
            filled=10, execution_price=5.0, limit_price=5.5, time_placed=intent.created_at)]
        return intent

    async def arrange_open_order(self):
        self._orders = [snaptrade_order_record(
            "boid-open", symbol="AAA", action="BUY", qty=10, status="ACCEPTED",
            open_q=10, limit_price=5.5)]


_HARNESSES = {"sim": SimHarness, "alert_only": AlertHarness, "alpaca": AlpacaHarness,
              "ibkr": IBKRHarness, "schwab": SchwabHarness, "snaptrade": SnapTradeHarness}


@pytest.fixture(params=list(_HARNESSES))
async def harness(request):
    h = _HARNESSES[request.param]()
    await h.setup()
    try:
        yield h
    finally:
        await h.teardown()
