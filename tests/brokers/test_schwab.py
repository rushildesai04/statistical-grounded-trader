"""SchwabBroker + SchwabTokenManager: auth rotation, ledger idempotency without a
client-order-id, POST-failure reconciliation, fills dedupe, long-only guard, health."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from sgt.brokers import SchwabBroker, SchwabTokenManager
from sgt.brokers.schwab_ledger import UNKNOWN as _UNKNOWN, ledger_mark
from sgt.core.interfaces import BrokerAuthError, BrokerRejectedError, BrokerUnavailableError
from sgt.core.types import OrderType, Side
from sgt.db.session import get_session
from sgt.accounts.models import OrderLedger

from brk_helpers import make_intent

UTC = timezone.utc
BASE = "https://api.schwabapi.com"
# Fixed "now" so tests never depend on the wall clock: intents are dated 2026-10-12 and
# the 60-day reconcile guard would otherwise start failing these tests in December.
TEST_NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


class _FakeSecrets:
    def __init__(self, **over):
        self._d = {
            "schwab_app_key": "ak",
            "schwab_app_secret": "as",
            "schwab_refresh_token": "RT0",
            "schwab_refresh_issued_at": TEST_NOW.isoformat(),
        }
        self._d.update(over)

    def get(self, name, required=True):
        return self._d.get(name)

    def set(self, name, value):
        self._d[name] = value


def _fixed_clock(ts):
    return lambda: ts


def _make_broker(store, *, clock=None, hashed=True):
    clock = clock or _fixed_clock(TEST_NOW)
    auth = SchwabTokenManager(secret_get=store.get, secret_set=store.set, clock=clock)
    broker = SchwabBroker("acct", "5678", clock=clock, auth=auth)
    if hashed:
        broker._account_hash = "HASH"
    return broker


def _token_ok(r, access="AT", refresh="RT1"):
    def tok(request):
        return httpx.Response(200, json={"access_token": access, "refresh_token": refresh,
                                         "expires_in": 1800})
    r.post("/v1/oauth/token").mock(side_effect=tok)


# --------------------------------------------------------------------------- token manager
async def test_refresh_persists_new_token_before_use():
    store = _FakeSecrets()
    seen: list[str] = []

    def tok(request):
        body = dict(x.split("=") for x in request.content.decode().split("&"))
        seen.append(body["refresh_token"])
        n = len(seen)
        return httpx.Response(200, json={"access_token": f"AT{n}", "refresh_token": f"RT{n}",
                                         "expires_in": 1800})

    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        r.post("/v1/oauth/token").mock(side_effect=tok)
        mgr = SchwabTokenManager(secret_get=store.get, secret_set=store.set)
        t1 = await mgr.access_token()
        # New refresh token persisted to the store (before the access token is handed out).
        assert store.get("schwab_refresh_token") == "RT1"
        assert t1 == "AT1"
        t2 = await mgr.access_token(force=True)
        assert t2 == "AT2"
        # The forced refresh used the ROTATED token, proving it was persisted and re-read.
        assert seen == ["RT0", "RT1"]
        assert store.get("schwab_refresh_token") == "RT2"
        await mgr.close()


async def test_refresh_rejected_maps_to_auth_error():
    store = _FakeSecrets()
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        r.post("/v1/oauth/token").mock(return_value=httpx.Response(400, json={"error": "bad"}))
        mgr = SchwabTokenManager(secret_get=store.get, secret_set=store.set)
        with pytest.raises(BrokerAuthError):
            await mgr.access_token()
        await mgr.close()


async def test_refresh_timeout_requires_reauth_no_blind_retry():
    store = _FakeSecrets()
    calls = {"n": 0}

    def tok(request):
        calls["n"] += 1
        raise httpx.ConnectTimeout("boom")

    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        r.post("/v1/oauth/token").mock(side_effect=tok)
        mgr = SchwabTokenManager(secret_get=store.get, secret_set=store.set)
        with pytest.raises(BrokerAuthError):
            await mgr.access_token()
        assert calls["n"] == 1  # a timed-out refresh is never retried (token may be spent)
        await mgr.close()


async def test_expired_refresh_token_requires_reauth_without_network():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    store = _FakeSecrets(schwab_refresh_issued_at=(now - timedelta(days=8)).isoformat())
    with respx.mock(base_url=BASE, assert_all_called=False):  # no token route: any POST would error
        mgr = SchwabTokenManager(secret_get=store.get, secret_set=store.set,
                                 clock=_fixed_clock(now))
        with pytest.raises(BrokerAuthError):
            await mgr.access_token()
        await mgr.close()


async def test_missing_refresh_token_requires_reauth():
    store = _FakeSecrets(schwab_refresh_token=None)
    with respx.mock(base_url=BASE, assert_all_called=False):
        mgr = SchwabTokenManager(secret_get=store.get, secret_set=store.set)
        with pytest.raises(BrokerAuthError):
            await mgr.access_token()
        await mgr.close()


# --------------------------------------------------------------------------- connect
@pytest.mark.parametrize("entries,ok", [
    ([], False),
    ([{"accountNumber": "99995678", "hashValue": "H1"}], True),
    ([{"accountNumber": "11115678", "hashValue": "H1"},
      {"accountNumber": "22225678", "hashValue": "H2"}], False),
])
async def test_account_hash_resolution(entries, ok):
    store = _FakeSecrets()
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.get("/trader/v1/accounts/accountNumbers").mock(
            return_value=httpx.Response(200, json=entries))
        broker = _make_broker(store, hashed=False)
        if ok:
            await broker.connect()
            assert broker._account_hash == "H1"
        else:
            with pytest.raises(BrokerRejectedError):
                await broker.connect()
        await broker.close()


# --------------------------------------------------------------------------- submit
async def test_submit_201_parses_location_and_records_ledger():
    store = _FakeSecrets()
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.post("/trader/v1/accounts/HASH/orders").mock(return_value=httpx.Response(
            201, headers={"Location": f"{BASE}/trader/v1/accounts/HASH/orders/987"}))
        broker = _make_broker(store)
        ack = await broker.submit(make_intent(cid="c-201"))
        assert ack.broker_order_id == "987"
        with get_session() as s:
            row = s.get(OrderLedger, "c-201")
            assert row.broker_order_id == "987" and row.status == "working"
        await broker.close()


async def test_post_timeout_order_landed_no_resubmit():
    store = _FakeSecrets()
    posts = {"n": 0}

    def post(request):
        posts["n"] += 1
        raise httpx.ConnectTimeout("boom")

    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.post("/trader/v1/accounts/HASH/orders").mock(side_effect=post)
        r.get("/trader/v1/accounts/HASH/orders").mock(return_value=httpx.Response(200, json=[{
            "orderId": "555", "status": "WORKING", "price": 5.5,
            "enteredTime": "2026-10-17T13:31:00.000Z",
            "orderLegCollection": [{"instruction": "BUY", "quantity": 10,
                                    "instrument": {"symbol": "AAA"}}]}]))
        broker = _make_broker(store)
        ack = await broker.submit(make_intent(cid="c-land"))
        assert ack.broker_order_id == "555"
        assert posts["n"] == 1  # landed via match -> never resubmitted
        await broker.close()


async def test_post_timeout_nothing_landed_single_resubmit():
    store = _FakeSecrets()
    posts = {"n": 0}

    def post(request):
        posts["n"] += 1
        if posts["n"] == 1:
            raise httpx.ConnectTimeout("boom")
        return httpx.Response(201, headers={
            "Location": f"{BASE}/trader/v1/accounts/HASH/orders/777"})

    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.post("/trader/v1/accounts/HASH/orders").mock(side_effect=post)
        r.get("/trader/v1/accounts/HASH/orders").mock(return_value=httpx.Response(200, json=[]))
        broker = _make_broker(store)
        ack = await broker.submit(make_intent(cid="c-resub"))
        assert ack.broker_order_id == "777"
        assert posts["n"] == 2  # confidently absent -> exactly one resubmit
        await broker.close()


async def test_post_timeout_ambiguous_marks_unknown_and_raises():
    store = _FakeSecrets()
    posts = {"n": 0}

    def post(request):
        posts["n"] += 1
        raise httpx.ConnectTimeout("boom")

    def two_matches():
        leg = [{"instruction": "BUY", "quantity": 10, "instrument": {"symbol": "AAA"}}]
        return [{"orderId": "a", "status": "WORKING", "price": 5.5,
                 "enteredTime": "2026-10-17T13:31:00.000Z", "orderLegCollection": leg},
                {"orderId": "b", "status": "WORKING", "price": 5.5,
                 "enteredTime": "2026-10-17T13:31:30.000Z", "orderLegCollection": leg}]

    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.post("/trader/v1/accounts/HASH/orders").mock(side_effect=post)
        r.get("/trader/v1/accounts/HASH/orders").mock(
            return_value=httpx.Response(200, json=two_matches()))
        broker = _make_broker(store)
        with pytest.raises(BrokerUnavailableError):
            await broker.submit(make_intent(cid="c-amb"))
        assert posts["n"] == 1  # never guessed / resubmitted
        with get_session() as s:
            assert s.get(OrderLedger, "c-amb").status == _UNKNOWN
        await broker.close()


async def test_resubmit_of_unknown_is_blocked():
    store = _FakeSecrets()
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _token_ok(r)
        broker = _make_broker(store)
        # Seed an UNKNOWN ledger row (a prior ambiguous submit).
        ledger_mark(make_intent(cid="c-unk"), _UNKNOWN, None, datetime.now(UTC))
        with pytest.raises(BrokerUnavailableError):
            await broker.submit(make_intent(cid="c-unk"))
        await broker.close()


# --------------------------------------------------------------------------- long-only
async def test_sell_exceeding_position_rejected_client_side():
    store = _FakeSecrets()
    posts = {"n": 0}
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.get("/trader/v1/accounts/HASH").mock(return_value=httpx.Response(200, json={
            "securitiesAccount": {"currentBalances": {"cashBalance": 0.0, "buyingPower": 0.0},
                                  "positions": [{"instrument": {"symbol": "AAA"},
                                                 "longQuantity": 5, "averagePrice": 4.0}]}}))
        r.get("/trader/v1/accounts/HASH/orders").mock(return_value=httpx.Response(200, json=[]))
        r.post("/trader/v1/accounts/HASH/orders").mock(
            side_effect=lambda req: (posts.__setitem__("n", posts["n"] + 1),
                                     httpx.Response(201))[1])
        broker = _make_broker(store)
        with pytest.raises(BrokerRejectedError):
            await broker.submit(make_intent(cid="c-sell", side=Side.SELL, qty=10))
        assert posts["n"] == 0  # rejected before any POST
        await broker.close()


async def test_reject_market_in_extended_hours():
    store = _FakeSecrets()
    intent = make_intent(cid="m", order_type=OrderType.MARKET, limit_price=None)
    object.__setattr__(intent, "extended_hours", True)
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _token_ok(r)
        broker = _make_broker(store)
        with pytest.raises(BrokerRejectedError):
            await broker.submit(intent)
        await broker.close()


# --------------------------------------------------------------------------- fills
async def test_fills_dedupe_across_polls():
    store = _FakeSecrets()
    order = {
        "orderId": "900", "status": "FILLED",
        "enteredTime": "2026-10-17T13:31:00.000Z",
        "orderLegCollection": [{"instruction": "BUY", "quantity": 10,
                                "instrument": {"symbol": "AAA"}}],
        "orderActivityCollection": [{"executionLegs": [
            {"legId": 1, "price": 5.0, "quantity": 6, "time": "2026-10-17T13:35:00.000Z"},
            {"legId": 2, "price": 5.1, "quantity": 4, "time": "2026-10-17T13:36:00.000Z"}]}],
    }
    with respx.mock(base_url=BASE, assert_all_called=False) as r:
        _token_ok(r)
        r.get("/trader/v1/accounts/HASH/orders").mock(
            return_value=httpx.Response(200, json=[order]))
        broker = _make_broker(store)
        first = [f async for f in broker.fills()]
        second = [f async for f in broker.fills()]
        assert [f.fill_id for f in first] == ["900-1", "900-2"]
        assert sum(f.qty for f in first) == 10
        assert second == []  # dedupe on deterministic fill_id
        await broker.close()


# --------------------------------------------------------------------------- health
async def test_health_warns_when_reauth_due_within_24h():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    store = _FakeSecrets(schwab_refresh_issued_at=(now - timedelta(days=6, hours=6)).isoformat())
    broker = _make_broker(store, clock=_fixed_clock(now))
    health = await broker.health()
    assert health.ok is False
    assert health.auth_expires_at is not None
    await broker.close()


async def test_health_ok_when_reauth_distant():
    now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)
    store = _FakeSecrets(schwab_refresh_issued_at=(now - timedelta(days=1)).isoformat())
    broker = _make_broker(store, clock=_fixed_clock(now))
    health = await broker.health()
    assert health.ok is True
    await broker.close()
