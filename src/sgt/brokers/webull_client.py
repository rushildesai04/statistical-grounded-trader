"""Signed httpx transport for Webull's official OpenAPI (US), production and sandbox.

Sources (read 2026-11-19; nothing here is reverse-engineered):
- https://developer.webull.com/apis/docs/authentication/signature  (signing; the worked example is a test vector)
- https://developer.webull.com/apis/docs/sdk and /rate-limits       (hosts, paths, limits)
- https://developer.webull.com/apis/docs/trade-api/stock            (stock order fields and allowed values)
- the official ``webull-openapi-python-sdk`` 3.0.4 (Apache-2.0), read for the request shapes: v3 paths, the
  ``category`` header on orders, cancel / detail by ``client_order_id``, and its HMAC-SHA256 signing variant.

Why not the SDK itself: it pins grpcio / protobuf / cryptography ranges and ships a ``requests`` transport that our
httpx test harness cannot replace; the signing is small and pinned here by Webull's own documented example.

Safety (fail closed):
- ``orders`` decides what ``place`` / ``replace`` / ``cancel`` may do: ``"none"`` (default) refuses them everywhere,
  ``"sandbox"`` allows them on the sandbox host only ("All trades are simulated, and no real orders will be placed"),
  ``"live"`` is the only value that lets an order reach the production host. Reads and ``preview`` are always allowed.
- Every request has a timeout; a response over MAX_BYTES or that is not JSON is an error; 429 and 5xx raise
  ``BrokerUnavailableError`` and a write is NEVER retried here (the caller reconciles by ``client_order_id``).
- The app secret and the access token are never logged, never put in an exception, never in ``repr``.

[VERIFY] with the owner's sandbox key before any adapter is built on this (the reference pages' response tables did
not render): response field names of every endpoint, order status values, whether the server accepts both signing
algorithms (the docs show HMAC-SHA1, the SDK sends HMAC-SHA256), the 2FA token flow for production keys.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from base64 import b64encode
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import quote, urlencode

import httpx

from sgt.core.interfaces import BrokerAuthError, BrokerError, BrokerRejectedError, BrokerUnavailableError

log = logging.getLogger(__name__)

PROD_HOST, SANDBOX_HOST = "api.webull.com", "api.sandbox.webull.com"
ALGORITHMS = {"HMAC-SHA1": (hashlib.md5, hashlib.sha1), "HMAC-SHA256": (hashlib.sha256, hashlib.sha256)}
SIGN_VERSION, API_VERSION = "1.0", "v3"
MAX_BYTES = 2_000_000
ORDER_MODES = ("none", "sandbox", "live")
P_ACCOUNTS, P_BALANCE, P_POSITIONS = "/trading/accounts/list", "/trading/assets/balances/get", "/trading/assets/positions/list"
P_PREVIEW, P_PLACE, P_REPLACE, P_CANCEL = ("/trading/orders/preview", "/trading/orders/place", "/trading/orders/replace",
                                           "/trading/orders/cancel")
P_ORDER, P_OPEN, P_EXECUTIONS = "/trading/orders/get", "/trading/orders/open-orders/list", "/trading/orders/executions/list"
P_SNAPSHOT = "/market-data/stocks/snapshots/list"
WRITES = frozenset({P_PLACE, P_REPLACE, P_CANCEL})

SYMBOL = re.compile(r"[A-Z]{1,5}(\.[A-Z])?")
CLIENT_ID = re.compile(r"[0-9A-Za-z_-]{1,32}")                 # the docs: at most 32 characters, unique per account
SIDES = ("BUY", "SELL", "SHORT")
ORDER_TYPES = ("MARKET", "LIMIT", "STOP_LOSS", "STOP_LOSS_LIMIT")
TIFS, SESSIONS = ("DAY", "GTC"), ("CORE", "ALL", "NIGHT")
_PRICE = re.compile(r"\d{1,7}(\.\d{1,4})?")
_QTY = re.compile(r"\d{1,9}(\.\d{1,5})?")


def compute_signature(path: str, query: dict[str, str], signed_headers: dict[str, str], body: str | None, secret: str,
                      algorithm: str = "HMAC-SHA256") -> str:
    """Webull's request signature. ``signed_headers``: x-app-key, x-signature-algorithm, x-signature-version,
    x-signature-nonce, x-timestamp and host. ``body``: the exact string that is sent (None / empty = no body).
    str1 = the query and the signed headers sorted by name, ``k=v`` joined by ``&``; str2 = the uppercase hex digest
    of the body (MD5 for HMAC-SHA1, SHA-256 for HMAC-SHA256); the string to sign = URL-encode(path & str1 [& str2]);
    the key = secret + ``&``; the result is base64."""
    digest, mac = ALGORITHMS[algorithm]
    params = {k.lower(): str(v) for k, v in signed_headers.items()}
    for k, v in query.items():
        params[k] = f"{params[k]}&{v}" if k in params else str(v)
    parts = [path, "&".join(f"{k}={v}" for k, v in sorted(params.items()))]
    if body:
        parts.append(digest(body.encode()).hexdigest().upper())
    to_sign = quote("&".join(parts), safe="")
    return b64encode(hmac.new((secret + "&").encode(), to_sign.encode(), mac).digest()).decode()


def stock_order(*, client_order_id: str, symbol: str, side: str, quantity: str, order_type: str = "LIMIT",
                limit_price: str | None = None, stop_price: str | None = None, time_in_force: str = "DAY",
                session: str = "CORE") -> dict[str, str]:
    """One plain US stock order in Webull's v3 shape, validated here so a malformed one never leaves the process.
    Quantities and prices are decimal STRINGS (as the API takes them). Share-quantity orders only (no dollar
    amounts, no combos, no algos)."""
    def bad(what: str) -> BrokerRejectedError:
        return BrokerRejectedError(f"webull order refused locally: {what}")
    if not isinstance(client_order_id, str) or not CLIENT_ID.fullmatch(client_order_id):
        raise bad("client_order_id must be 1-32 letters, digits, _ or -")
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise bad("symbol is not a plain US ticker")
    if side not in SIDES or order_type not in ORDER_TYPES or time_in_force not in TIFS or session not in SESSIONS:
        raise bad("side, order type, time in force or session is not an allowed value")
    if not isinstance(quantity, str) or not _QTY.fullmatch(quantity) or float(quantity) <= 0:
        raise bad("quantity must be a positive decimal string")
    need_limit, need_stop = order_type in ("LIMIT", "STOP_LOSS_LIMIT"), order_type in ("STOP_LOSS", "STOP_LOSS_LIMIT")
    for name, px, need in (("limit_price", limit_price, need_limit), ("stop_price", stop_price, need_stop)):
        if need != (px is not None):
            raise bad(f"{name} is {'required' if need else 'not allowed'} for {order_type}")
        if px is not None and (not isinstance(px, str) or not _PRICE.fullmatch(px) or float(px) <= 0):
            raise bad(f"{name} must be a positive decimal string")
    if order_type == "MARKET" and session != "CORE":
        raise bad("a market order is for the regular session only")
    o = {"combo_type": "NORMAL", "client_order_id": client_order_id, "symbol": symbol, "instrument_type": "EQUITY",
         "market": "US", "order_type": order_type, "quantity": quantity, "support_trading_session": session, "side": side,
         "time_in_force": time_in_force, "entrust_type": "QTY"}
    if limit_price is not None:
        o["limit_price"] = limit_price
    if stop_price is not None:
        o["stop_price"] = stop_price
    return o


class WebullClient:
    """One app key against one host. Reads, previews and (when ``orders`` allows it) order writes."""

    def __init__(self, *, app_key: str, app_secret: str, sandbox: bool, orders: str = "none", access_token: str | None = None,
                 algorithm: str = "HMAC-SHA256", timeout_s: float = 10.0, http_client: httpx.AsyncClient | None = None,
                 write_min_interval_s: float = 0.25, monotonic: Callable[[], float] | None = None,
                 sleep: Callable[[float], Awaitable[None]] | None = None, now: Callable[[], datetime] | None = None,
                 nonce: Callable[[], str] | None = None) -> None:
        if not app_key or not app_secret:
            raise ValueError("WebullClient requires an app key and an app secret")
        if orders not in ORDER_MODES or algorithm not in ALGORITHMS:
            raise ValueError("unknown orders mode or signing algorithm")
        if orders == "sandbox" and not sandbox:
            raise ValueError("orders='sandbox' needs the sandbox host: a simulated order must never reach production")
        self.sandbox, self.orders = sandbox, orders
        self._host = SANDBOX_HOST if sandbox else PROD_HOST
        self._key, self._secret, self._token, self._algorithm = app_key, app_secret, access_token, algorithm
        self._timeout, self._client = timeout_s, http_client
        self._gap, self._last_write = write_min_interval_s, None
        self._monotonic, self._sleep = monotonic or time.monotonic, sleep or asyncio.sleep
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._nonce = nonce or (lambda: uuid.uuid4().hex)

    def __repr__(self) -> str:                                   # never the key, the secret or the token
        return f"WebullClient(host={self._host!r}, orders={self.orders!r})"

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _may_write(self, path: str) -> None:
        if path not in WRITES:
            return
        if self.orders == "none":
            raise BrokerRejectedError("webull orders are switched off for this client (orders='none')")
        if self.orders == "sandbox" and self._host != SANDBOX_HOST:
            raise BrokerRejectedError("a simulated order must never reach the production host")
        if self.orders != "live" and self._host == PROD_HOST:
            raise BrokerRejectedError("production orders need orders='live'")

    async def call(self, method: str, path: str, *, query: dict[str, Any] | None = None, body: dict[str, Any] | None = None,
                   category: str | None = None) -> Any:
        """One signed, bounded request; decoded JSON back. Raises ``BrokerError`` subclasses, never returns an error."""
        self._may_write(path)
        if path in WRITES:                                       # spacing between writes (limit: 600 per 60 s)
            wait = 0.0 if self._last_write is None else self._gap - (self._monotonic() - self._last_write)
            if wait > 0:
                await self._sleep(wait)
            self._last_write = self._monotonic()
        q = {k: str(v) for k, v in (query or {}).items() if v is not None}
        raw = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        signed = {"x-app-key": self._key, "x-timestamp": self._now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "x-signature-version": SIGN_VERSION, "x-signature-algorithm": self._algorithm, "x-signature-nonce": self._nonce()}
        headers = {**signed, "x-version": API_VERSION, "Accept": "application/json",
                   "x-signature": compute_signature(path, q, {**signed, "host": self._host}, raw, self._secret, self._algorithm)}
        if raw is not None:
            headers["Content-Type"] = "application/json"
        if self._token:
            headers["x-access-token"] = self._token
        if category:
            headers["category"] = category
        url = f"https://{self._host}{path}" + (f"?{urlencode(q)}" if q else "")
        try:
            r = await self._http().request(method, url, headers=headers, content=None if raw is None else raw.encode(),
                                           timeout=self._timeout)
        except httpx.HTTPError as e:
            raise BrokerUnavailableError(f"webull {method} {path}: {type(e).__name__}") from None
        if len(r.content) > MAX_BYTES:
            raise BrokerUnavailableError(f"webull {method} {path}: answer over {MAX_BYTES} bytes")
        if r.status_code == 429 or r.status_code >= 500:
            raise BrokerUnavailableError(f"webull {method} {path}: HTTP {r.status_code}")
        if r.status_code in (401, 403):
            raise BrokerAuthError(f"webull {method} {path}: HTTP {r.status_code} (key, signature or token refused)")
        if r.status_code != 200:                                 # 417 = "a business logic error" (docs); others: refused
            raise BrokerRejectedError(f"webull {method} {path}: HTTP {r.status_code} {_reason(r)}")
        try:
            return r.json()
        except ValueError:
            raise BrokerError(f"webull {method} {path}: the answer is not JSON") from None

    # ------------------------------------------------------------------ reads
    async def accounts(self) -> Any:
        return await self.call("GET", P_ACCOUNTS)

    async def balance(self, account_id: str, currency: str = "USD") -> Any:
        return await self.call("GET", P_BALANCE, query={"account_id": account_id, "total_asset_currency": currency})

    async def positions(self, account_id: str) -> Any:
        return await self.call("GET", P_POSITIONS, query={"account_id": account_id})

    async def order(self, account_id: str, client_order_id: str) -> Any:
        return await self.call("GET", P_ORDER, query={"account_id": account_id, "client_order_id": client_order_id})

    async def open_orders(self, account_id: str, pagination_key: str | None = None) -> Any:
        return await self.call("GET", P_OPEN, query={"account_id": account_id, "pagination_key": pagination_key})

    async def snapshot(self, symbols: list[str], category: str = "US_STOCK") -> Any:
        ok = [s for s in symbols if isinstance(s, str) and SYMBOL.fullmatch(s)][:100]
        if not ok:
            return []
        return await self.call("GET", P_SNAPSHOT, query={"symbols": ",".join(ok), "category": category,
                                                         "extend_hour_required": "false", "overnight_required": "false"})

    # ------------------------------------------------------------------ orders
    async def preview(self, account_id: str, order: dict[str, str]) -> Any:
        """The broker's own estimate of an order ("Calculates the estimated amount and cost"). Places nothing."""
        return await self.call("POST", P_PREVIEW, body={"account_id": account_id, "new_orders": [order]}, category=_category(order))

    async def place(self, account_id: str, order: dict[str, str]) -> Any:
        return await self.call("POST", P_PLACE, body={"account_id": account_id, "new_orders": [order]}, category=_category(order))

    async def cancel(self, account_id: str, client_order_id: str) -> Any:
        return await self.call("POST", P_CANCEL, body={"account_id": account_id, "client_order_id": client_order_id})


def _category(order: dict[str, str]) -> str:
    return f"{order['market']}_{order['instrument_type']}"       # the SDK's header on order requests, e.g. US_EQUITY


def _reason(r: httpx.Response) -> str:
    """The broker's own short error text (untrusted): bounded, one line, never the request."""
    try:
        j = r.json()
        msg = j.get("message") or j.get("msg") or j.get("error_code") or "" if isinstance(j, dict) else ""
    except ValueError:
        msg = ""
    return re.sub(r"[\x00-\x1f\x7f]+", " ", str(msg))[:160]
