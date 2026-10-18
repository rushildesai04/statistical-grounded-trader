"""Signed httpx transport for the SnapTrade API (https://api.snaptrade.com/api/v1).

A small, auditable client rather than the generated ``snaptrade-python-sdk``: that SDK
ships urllib3/aiohttp transports that cannot be swapped for an httpx ``MockTransport``,
so it is untestable against our respx harness. The signing scheme here is the one
SnapTrade documents for callers who hit the API directly (see the [VERIFY] block in
``snaptrade.py``); it is byte-for-byte reproduced from their reference implementation and
pinned by a hand-computed test vector in ``tests/brokers/test_snaptrade.py``.

Every request is signed with HMAC-SHA256 over a canonical JSON payload and bounded by
``settings.http_timeout_s``. Trade requests (place/cancel) are serialized through a
per-account rate limiter (default 1 request/second) with an injectable clock so the
spacing is deterministic under test. Secrets (``consumerKey``, ``userSecret``) are never
logged and never placed anywhere but the signed query string / signature header.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from base64 import b64encode
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import httpx

from sgt.core.interfaces import (
    BrokerAuthError,
    BrokerRejectedError,
    BrokerUnavailableError,
)
from sgt.core.secrets import redact

log = logging.getLogger(__name__)

API_PREFIX = "/api/v1"
DEFAULT_BASE_URL = "https://api.snaptrade.com/api/v1"
_MAX_RETRIES = 3
_BACKOFF_BASE_S = 0.25
_RELINK_HINT = (
    "SnapTrade rejected the credentials (401/403). The brokerage connection may need "
    "to be re-linked via the connection portal (scripts/snaptrade_connect.py)."
)


def compute_signature(subpath: str, query: str, consumer_key: str, body: Any) -> str:
    """HMAC-SHA256 request signature per SnapTrade's documented scheme.

    The signed payload is a canonical JSON object ``{"content", "path", "query"}`` with
    keys sorted and no insignificant whitespace; ``content`` is ``null`` for an empty or
    bodyless request. ``path`` includes the ``/api/v1`` prefix; ``query`` is the exact
    query string sent on the URL (without the leading ``?``). The digest is base64-encoded
    and sent in the ``Signature`` header.
    """
    sig_object = {
        "content": None if body is None or body == {} else body,
        "path": f"{API_PREFIX}{subpath}",
        "query": query,
    }
    sig_content = json.dumps(sig_object, separators=(",", ":"), sort_keys=True)
    digest = hmac.new(consumer_key.encode(), sig_content.encode(), hashlib.sha256).digest()
    return b64encode(digest).decode()


class _TradeRateLimiter:
    """Serializes trade requests to at most one per ``min_interval`` seconds.

    Clock and sleep are injectable so tests assert the spacing deterministically without
    real time. ``monotonic`` must be monotonic; wall-clock is never used here.
    """

    def __init__(
        self,
        min_interval_s: float,
        *,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._min_interval = min_interval_s
        self._monotonic = monotonic or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._lock = asyncio.Lock()
        self._next_allowed: float | None = None

    async def acquire(self) -> None:
        async with self._lock:
            now = self._monotonic()
            if self._next_allowed is not None and now < self._next_allowed:
                await self._sleep(self._next_allowed - now)
                now = self._monotonic()
            self._next_allowed = max(now, self._next_allowed or now) + self._min_interval


class SnapTradeClient:
    """Minimal signed async client. One instance per (partner key, user) pair.

    ``clientId``/``consumerKey`` are partner credentials; ``userId``/``userSecret`` scope
    every call to one SnapTrade end-user. User-scoped calls append ``userId``/``userSecret``
    to the signed query string, exactly as SnapTrade requires.
    """

    def __init__(
        self,
        *,
        client_id: str,
        consumer_key: str,
        user_id: str,
        user_secret: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout_s: float = 10.0,
        http_client: httpx.AsyncClient | None = None,
        trade_min_interval_s: float = 1.0,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not (client_id and consumer_key and user_id):
            raise ValueError("SnapTradeClient requires client_id, consumer_key, user_id")
        # user_secret may be empty ONLY during the registration bootstrap (register_user is
        # not user-scoped); user-scoped calls below fail closed if it is still unset.
        self._client_id = client_id
        self._consumer_key = consumer_key
        self._user_id = user_id
        self._user_secret = user_secret
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_s
        self._owns_client = http_client is None
        self._client = http_client
        self._limiter = _TradeRateLimiter(trade_min_interval_s, monotonic=monotonic, sleep=sleep)

    # ------------------------------------------------------------------ transport
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        return self._client

    def _build_query(self, user_scoped: bool, extra: dict[str, Any] | None) -> str:
        # Insertion order IS the signed order; urlencode preserves it. clientId and the
        # Unix timestamp are always first, then user credentials, then any extra params.
        pairs: list[tuple[str, str]] = [
            ("clientId", self._client_id),
            ("timestamp", str(int(time.time()))),
        ]
        if user_scoped:
            if not self._user_secret:
                raise BrokerAuthError(
                    "SnapTrade user_secret is not set; register the user first "
                    "(scripts/snaptrade_connect.py)")
            pairs.append(("userId", self._user_id))
            pairs.append(("userSecret", self._user_secret))
        for k, v in (extra or {}).items():
            pairs.append((k, str(v)))
        return urlencode(pairs)

    async def call(
        self,
        method: str,
        subpath: str,
        *,
        query_params: dict[str, Any] | None = None,
        body: Any = None,
        user_scoped: bool = True,
        retry: bool = True,
        rate_limited: bool = False,
    ) -> Any:
        """One signed, bounded request. Returns decoded JSON (dict or list).

        ``retry`` applies only to transient failures (429/5xx/timeout) and must be False
        for non-idempotent writes whose caller performs its own reconciliation.
        ``rate_limited`` gates trade requests through the per-account spacing limiter.
        """
        if rate_limited:
            await self._limiter.acquire()
        query = self._build_query(user_scoped, query_params)
        signature = compute_signature(subpath, query, self._consumer_key, body)
        url = f"{self._base_url}{subpath}?{query}"
        headers = {"Signature": signature, "Content-Type": "application/json", "Accept": "application/json"}
        client = self._http()
        last_exc: Exception | None = None
        attempts = _MAX_RETRIES if retry else 1
        for attempt in range(attempts):
            try:
                resp = await client.request(
                    method, url, headers=headers,
                    content=json.dumps(body).encode() if body is not None else None,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = BrokerUnavailableError(f"{method} {subpath}: {type(exc).__name__}")
                log.warning("snaptrade %s %s transport error (client %s): %s",
                            method, subpath, redact(self._client_id), type(exc).__name__)
                if attempt == attempts - 1:
                    raise last_exc from exc
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = BrokerUnavailableError(f"{method} {subpath}: HTTP {resp.status_code}")
                if attempt == attempts - 1:
                    raise last_exc
                await asyncio.sleep(self._retry_after(resp, attempt))
                continue
            return self._decode(resp, method, subpath)
        assert last_exc is not None
        raise last_exc

    @staticmethod
    def _retry_after(resp: httpx.Response, attempt: int) -> float:
        raw = resp.headers.get("Retry-After")
        if raw:
            try:
                return min(float(raw), 30.0)
            except ValueError:
                pass
        return _BACKOFF_BASE_S * (2 ** attempt)

    def _decode(self, resp: httpx.Response, method: str, subpath: str) -> Any:
        code = resp.status_code
        if code < 400:
            if not resp.content:
                return {}
            try:
                return resp.json()
            except ValueError as exc:
                raise BrokerUnavailableError(f"{method} {subpath}: non-JSON {code}") from exc
        body = resp.text[:500]
        if code in (401, 403):
            raise BrokerAuthError(f"{method} {subpath}: HTTP {code}. {_RELINK_HINT}")
        raise BrokerRejectedError(f"{method} {subpath}: HTTP {code} {body}")

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ endpoints
    async def register_user(self) -> dict[str, Any]:
        # userSecret is returned here (first and only time); never signed into THIS request.
        return await self.call("POST", "/snapTrade/registerUser",
                               body={"userId": self._user_id}, user_scoped=False)

    async def login_portal_url(self, *, connection_type: str | None = None) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if connection_type:
            body["connectionType"] = connection_type
        return await self.call("POST", "/snapTrade/login", body=body or None)

    async def list_accounts(self) -> list[dict[str, Any]]:
        res = await self.call("GET", "/accounts/all")
        return res if isinstance(res, list) else []

    async def list_authorizations(self) -> list[dict[str, Any]]:
        res = await self.call("GET", "/authorizations")
        return res if isinstance(res, list) else []

    async def get_authorization(self, authorization_id: str) -> dict[str, Any]:
        res = await self.call("GET", f"/authorizations/{authorization_id}")
        return res if isinstance(res, dict) else {}

    async def get_positions(self, account_id: str) -> list[dict[str, Any]]:
        res = await self.call("GET", f"/accounts/{account_id}/positions/all")
        return res if isinstance(res, list) else []

    async def get_balances(self, account_id: str) -> list[dict[str, Any]]:
        res = await self.call("GET", f"/accounts/{account_id}/balances")
        return res if isinstance(res, list) else []

    async def get_orders(self, account_id: str, *, state: str = "all", days: int = 7) -> list[dict[str, Any]]:
        res = await self.call("GET", f"/accounts/{account_id}/orders",
                              query_params={"state": state, "days": days})
        return res if isinstance(res, list) else []

    async def place_force_order(self, body: dict[str, Any]) -> dict[str, Any]:
        # Non-idempotent: retry=False. The broker reconciles via get_orders before resubmit.
        res = await self.call("POST", "/trade/place", body=body, retry=False, rate_limited=True)
        return res if isinstance(res, dict) else {}

    async def cancel_order(self, account_id: str, brokerage_order_id: str) -> dict[str, Any]:
        res = await self.call(
            "POST", f"/accounts/{account_id}/trading/cancel",
            body={"brokerage_order_id": brokerage_order_id}, retry=False, rate_limited=True)
        return res if isinstance(res, dict) else {}
