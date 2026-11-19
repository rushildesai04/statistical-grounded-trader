"""Webull OpenAPI client: the documented signing vector, exact bytes signed = bytes sent, order safety modes,
error mapping, bounds and secret hygiene. No network (httpx MockTransport)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import httpx
import pytest

from sgt.brokers import webull_client as wc
from sgt.brokers.webull_client import WebullClient, compute_signature, stock_order
from sgt.core.interfaces import BrokerAuthError, BrokerError, BrokerRejectedError, BrokerUnavailableError

SECRET = "0f50a2e853334a9aae1a783bee120c1f"                       # the example secret printed in Webull's docs
DOC_HEADERS = {"x-app-key": "776da210ab4a452795d74e726ebd74b6", "x-timestamp": "2022-01-04T03:55:31Z", "x-signature-version": "1.0",
               "x-signature-nonce": "48ef5afed43d4d91ae514aaeafbc29ba", "host": "api.webull.com"}
DOC_QUERY = {"a1": "webull", "a2": "123", "a3": "xxx", "q1": "yyy"}
DOC_BODY = '{"k1":123,"k2":"this is the api request body","k3":true,"k4":{"foo":[1,2]}}'
NOW = datetime(2026, 11, 19, 15, 0, 5, tzinfo=timezone.utc)


def test_the_signature_is_the_one_in_webulls_documentation():
    sha1 = compute_signature("/trade/place_order", DOC_QUERY, {**DOC_HEADERS, "x-signature-algorithm": "HMAC-SHA1"}, DOC_BODY,
                             SECRET, "HMAC-SHA1")
    assert sha1 == "kvlS6opdZDhEBo5jq40nHYXaLvM="                 # the docs' expected value, character for character
    # the variant the official SDK 3.0.4 sends (same string, SHA-256 body digest, HMAC-SHA256); derived from its source
    sha256 = compute_signature("/trade/place_order", DOC_QUERY, {**DOC_HEADERS, "x-signature-algorithm": "HMAC-SHA256"}, DOC_BODY,
                               SECRET)
    assert sha256 == "WmKFpDtQMSUhCYjmgA66EX5dQo+pS4qOwu3Kl0tb6KU="
    no_body = compute_signature("/trading/accounts/list", {}, {**DOC_HEADERS, "x-signature-algorithm": "HMAC-SHA1"}, None, SECRET, "HMAC-SHA1")
    assert no_body != sha1 and len(no_body) == 28


def client(handler, **kw) -> WebullClient:
    kw.setdefault("app_key", "k" * 32)
    kw.setdefault("app_secret", SECRET)
    kw.setdefault("sandbox", True)
    kw.setdefault("write_min_interval_s", 0.0)
    return WebullClient(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), now=lambda: NOW,
                        nonce=lambda: "n" * 32, **kw)


ORDER = dict(client_order_id="abc123", symbol="AAPL", side="BUY", quantity="1", order_type="LIMIT", limit_price="1.00")


async def test_what_is_signed_is_exactly_what_is_sent():
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, json={"ok": True})
    c = client(handler, access_token="tok-secret-1")
    assert await c.preview("ACCT1", stock_order(**ORDER)) == {"ok": True}
    r = seen[0]
    assert (r.method, r.url.host, r.url.path) == ("POST", "api.sandbox.webull.com", "/trading/orders/preview")
    body = r.content.decode()
    assert body == json.dumps(json.loads(body), separators=(",", ":")) and json.loads(body)["new_orders"][0]["client_order_id"] == "abc123"
    h = r.headers
    assert (h["x-version"], h["x-signature-version"], h["x-signature-algorithm"], h["category"]) == ("v3", "1.0", "HMAC-SHA256", "US_EQUITY")
    assert h["x-timestamp"] == "2026-11-19T15:00:05Z" and h["x-access-token"] == "tok-secret-1" and h["content-type"] == "application/json"
    signed = {k: h[k] for k in ("x-app-key", "x-timestamp", "x-signature-version", "x-signature-algorithm", "x-signature-nonce")}
    assert h["x-signature"] == compute_signature("/trading/orders/preview", {}, {**signed, "host": "api.sandbox.webull.com"}, body, SECRET)
    await c.balance("ACCT1")
    g = seen[1]
    assert g.method == "GET" and dict(g.url.params) == {"account_id": "ACCT1", "total_asset_currency": "USD"} and not g.content
    assert "content-type" not in g.headers and "category" not in g.headers
    assert g.headers["x-signature"] == compute_signature(
        "/trading/assets/balances/get", dict(g.url.params),
        {**{k: g.headers[k] for k in signed}, "host": "api.sandbox.webull.com"}, None, SECRET)


async def test_no_order_reaches_production_unless_the_client_was_built_for_it():
    hits = []

    def handler(req: httpx.Request) -> httpx.Response:
        hits.append((req.url.host, req.url.path))
        return httpx.Response(200, json={})
    o = stock_order(**ORDER)
    for kw in (dict(sandbox=True), dict(sandbox=False)):                         # the default: orders="none"
        c = client(handler, **kw)
        for write in (c.place("A", o), c.cancel("A", "abc123")):
            with pytest.raises(BrokerRejectedError):
                await write
        await c.preview("A", o)                                                  # a preview places nothing: allowed
    assert [p for _, p in hits] == ["/trading/orders/preview"] * 2
    with pytest.raises(ValueError):
        client(handler, sandbox=False, orders="sandbox")                          # simulated orders, production host
    with pytest.raises(ValueError):
        client(handler, orders="yes")
    hits.clear()
    paper = client(handler, sandbox=True, orders="sandbox")
    await paper.place("A", o)
    await paper.cancel("A", "abc123")
    assert hits == [("api.sandbox.webull.com", "/trading/orders/place"), ("api.sandbox.webull.com", "/trading/orders/cancel")]
    hits.clear()
    live = client(handler, sandbox=False, orders="live")
    await live.place("A", o)
    assert hits == [("api.webull.com", "/trading/orders/place")]


async def test_errors_are_typed_and_a_write_is_never_retried():
    calls = []

    def answer(code, **kw):
        def handler(req):
            calls.append(req.url.path)
            return httpx.Response(code, **kw)
        return handler
    for code, err in ((429, BrokerUnavailableError), (503, BrokerUnavailableError), (401, BrokerAuthError), (403, BrokerAuthError),
                      (417, BrokerRejectedError), (400, BrokerRejectedError)):
        calls.clear()
        with pytest.raises(err):
            await client(answer(code, json={"message": "nope\n" + "x" * 500}), orders="sandbox").place("A", stock_order(**ORDER))
        assert calls == ["/trading/orders/place"]                                  # one attempt, whatever the answer
    with pytest.raises(BrokerRejectedError) as e:
        await client(answer(417, json={"message": "INSUFFICIENT\nFUNDS " + "x" * 500})).accounts()
    assert "INSUFFICIENT FUNDS" in str(e.value) and len(str(e.value)) < 260 and "\n" not in str(e.value)
    with pytest.raises(BrokerError):
        await client(answer(200, content=b"<html>not json</html>")).accounts()
    with pytest.raises(BrokerUnavailableError):
        await client(answer(200, content=b"[" + b"1," * (wc.MAX_BYTES // 2) + b"1]")).accounts()

    def boom(req):
        raise httpx.ConnectTimeout("slow")
    with pytest.raises(BrokerUnavailableError):
        await client(boom).accounts()


async def test_the_secret_and_the_token_never_appear_in_logs_errors_or_repr(caplog):
    caplog.set_level(logging.DEBUG)
    c = client(lambda req: httpx.Response(401, json={"message": "bad"}), access_token="tok-secret-1")
    with pytest.raises(BrokerAuthError) as e:
        await c.accounts()
    text = caplog.text + str(e.value) + repr(c) + str(c.__dict__.keys())
    assert SECRET not in text and "tok-secret-1" not in text and "k" * 32 not in text
    assert repr(c) == "WebullClient(host='api.sandbox.webull.com', orders='none')"
    with pytest.raises(ValueError):
        WebullClient(app_key="", app_secret="x", sandbox=True)


def test_a_malformed_order_never_leaves_the_process():
    ok = stock_order(**ORDER)
    assert ok == {"combo_type": "NORMAL", "client_order_id": "abc123", "symbol": "AAPL", "instrument_type": "EQUITY", "market": "US",
                  "order_type": "LIMIT", "quantity": "1", "support_trading_session": "CORE", "side": "BUY", "time_in_force": "DAY",
                  "entrust_type": "QTY", "limit_price": "1.00"}
    assert stock_order(**{**ORDER, "order_type": "MARKET", "limit_price": None}).get("limit_price") is None
    assert stock_order(**{**ORDER, "side": "SHORT", "order_type": "STOP_LOSS_LIMIT", "stop_price": "2.5"})["stop_price"] == "2.5"
    bad = [dict(client_order_id="x" * 33), dict(client_order_id="a b"), dict(symbol="AAPL;DROP"), dict(symbol="aapl"), dict(side="buy"),
           dict(quantity="0"), dict(quantity="-1"), dict(quantity=1), dict(quantity="1e9"), dict(limit_price="abc"), dict(limit_price=None),
           dict(order_type="MARKET"), dict(order_type="TWAP"), dict(time_in_force="IOC"), dict(session="24H"), dict(stop_price="3"),
           dict(order_type="MARKET", limit_price=None, session="ALL"), dict(limit_price="0")]
    for change in bad:
        with pytest.raises(BrokerRejectedError):
            stock_order(**{**ORDER, **change})


async def test_snapshot_asks_only_for_plain_tickers_and_writes_are_spaced():
    seen = []
    c = client(lambda req: seen.append(req) or httpx.Response(200, json=[]))
    assert await c.snapshot(["AAPL", "bad symbol", "X&y=1", "BRK.B", None]) == []
    assert seen[0].url.params["symbols"] == "AAPL,BRK.B" and await c.snapshot(["nope!"]) == [] and len(seen) == 1
    t, slept = [100.0], []

    async def sleep(s):
        slept.append(round(s, 3))
        t[0] += s
    w = client(lambda req: httpx.Response(200, json={}), orders="sandbox", write_min_interval_s=0.25, monotonic=lambda: t[0], sleep=sleep)
    await w.place("A", stock_order(**ORDER))
    await w.cancel("A", "abc123")
    assert slept == [0.25]


def test_the_check_script_hides_identifiers_and_never_orders_in_a_real_account(capsys):
    import webull_check as chk

    got = chk.shape({"account_id": "5XJ12345", "account_number": "8812", "currency": "USD", "status": "NORMAL", "total": 1.5,
                     "positions": [{"symbol": "AAPL", "qty": "3", "position_id": "p1"}], "note": "free text here that is long"})
    assert got == {"account_id": "<hidden>", "account_number": "<hidden>", "currency": "USD", "status": "NORMAL", "total": "float",
                   "positions": [{"symbol": "AAPL", "qty": "<str 1>", "position_id": "<hidden>"}, "... 1 items"], "note": "<str 27>"}
    assert chk.first_account([{"account_id": "A1"}]) == "A1" and chk.first_account({"data": [{"account_id": "B2"}]}) == "B2"
    assert chk.first_account({"x": 1}) is None and chk.first_account("junk") is None
    assert chk.main(["--paper-order"]) == 2 and "never places an order in a real account" in capsys.readouterr().out
