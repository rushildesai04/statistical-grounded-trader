"""Alpaca Trading API v2 adapter (paper and live).

Credentials come only from ``sgt.core.secrets`` (keychain or ``SGT_SECRET_*``):
paper uses ``alpaca_key_id`` / ``alpaca_secret_key``; live uses the distinct
``alpaca_live_key_id`` / ``alpaca_live_secret_key`` so a paper key can never reach the
live endpoint. Base URLs come from settings (``alpaca_paper_url`` / ``alpaca_live_url``).

Every request is bounded by ``settings.http_timeout_s``. Idempotent reads retry with
backoff on 429/5xx/timeout; ``submit`` only retries after confirming via a
client-order-id lookup that the order did not already land, so a retry can never
double-submit.

[VERIFY] against official Alpaca docs before enabling a live account:
- Endpoint paths and query params: ``GET /v2/orders:by_client_order_id``,
  ``GET /v2/account/activities/FILL`` (``after`` / ``direction=asc`` cursor semantics).
- The exact 422 duplicate-id message substring ("client_order_id must be unique").
- Account fields used: ``cash``, ``buying_power``, ``non_marginable_buying_power``,
  ``shorting_enabled`` (live connect refuses unless it is exactly ``false``).
- FILL activity fields: ``id``, ``transaction_time``, ``symbol``, ``side``, ``qty``,
  ``price``, ``order_id`` (and that ``id`` is a stable per-execution cursor key).
- Order status string -> OrderStatus mapping (new/accepted/partially_filled/filled/
  canceled/expired/rejected/pending_*).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

import httpx

from sgt.config import get_settings
from sgt.core.interfaces import (
    AdapterHealth,
    BrokerAdapter,
    BrokerAuthError,
    BrokerRejectedError,
    BrokerUnavailableError,
)
from sgt.core.secrets import get_secret, redact
from sgt.core.types import (
    AccountState,
    Fill,
    OrderAck,
    OrderIntent,
    OrderStatus,
    OrderType,
    Position,
    Side,
    TimeInForce,
)

log = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE_S = 0.25
_DUP_ID_MSG = "client_order_id must be unique"

_STATUS_MAP = {
    "new": OrderStatus.WORKING,
    "accepted": OrderStatus.WORKING,
    "pending_new": OrderStatus.PENDING,
    "accepted_for_bidding": OrderStatus.WORKING,
    "held": OrderStatus.WORKING,
    "partially_filled": OrderStatus.PARTIALLY_FILLED,
    "filled": OrderStatus.FILLED,
    "done_for_day": OrderStatus.WORKING,
    "canceled": OrderStatus.CANCELED,
    "pending_cancel": OrderStatus.WORKING,
    "expired": OrderStatus.EXPIRED,
    "replaced": OrderStatus.CANCELED,
    "pending_replace": OrderStatus.WORKING,
    "stopped": OrderStatus.WORKING,
    "rejected": OrderStatus.REJECTED,
    "suspended": OrderStatus.WORKING,
    "calculated": OrderStatus.WORKING,
}


class AlpacaBroker(BrokerAdapter):
    name = "alpaca"
    supports_extended_hours = True
    supports_broker_side_stops = True

    def __init__(
        self,
        account_id: str,
        paper: bool,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.account_id = account_id
        self.paper = paper
        self.is_paper = paper
        settings = get_settings()
        self._base_url = settings.alpaca_paper_url if paper else settings.alpaca_live_url
        self._timeout = settings.http_timeout_s
        key_name = "alpaca_key_id" if paper else "alpaca_live_key_id"
        secret_name = "alpaca_secret_key" if paper else "alpaca_live_secret_key"
        self._key_id = get_secret(key_name)
        self._secret_key = get_secret(secret_name)
        assert self._key_id is not None and self._secret_key is not None
        self._headers = {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret_key,
            "Content-Type": "application/json",
        }
        self._owns_client = client is None
        self._client = client
        self._fill_cursor: str | None = None
        self._seen_fill_ids: set[str] = set()

    # ------------------------------------------------------------------ transport
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url, headers=self._headers,
                timeout=httpx.Timeout(self._timeout),
            )
        return self._client

    async def _request(self, method: str, path: str, *, retry: bool,
                       json: dict[str, Any] | None = None,
                       params: dict[str, Any] | None = None) -> httpx.Response:
        """One bounded HTTP call with mapped errors; retries only when ``retry``."""
        client = self._http()
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES if retry else 1):
            try:
                resp = await client.request(method, path, json=json, params=params)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                log.warning("alpaca %s %s transport error (key %s): %s",
                            method, path, redact(self._key_id), type(exc).__name__)
                if not retry or attempt == _MAX_RETRIES - 1:
                    raise BrokerUnavailableError(f"{method} {path}: {type(exc).__name__}") from exc
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                continue
            if resp.status_code in (429,) or resp.status_code >= 500:
                last_exc = BrokerUnavailableError(f"{method} {path}: HTTP {resp.status_code}")
                if not retry or attempt == _MAX_RETRIES - 1:
                    raise last_exc
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                continue
            return resp
        assert last_exc is not None
        raise last_exc

    def _raise_for_status(self, resp: httpx.Response, context: str) -> None:
        code = resp.status_code
        if code < 400:
            return
        body = resp.text[:500]
        if code in (401, 403) and "insufficient" not in body.lower():
            raise BrokerAuthError(f"{context}: HTTP {code}")
        if code in (403, 422):
            raise BrokerRejectedError(f"{context}: HTTP {code} {body}")
        if code == 429 or code >= 500:
            raise BrokerUnavailableError(f"{context}: HTTP {code}")
        raise BrokerRejectedError(f"{context}: HTTP {code} {body}")

    # ------------------------------------------------------------------ adapter API
    async def connect(self) -> None:
        resp = await self._request("GET", "/v2/account", retry=True)
        self._raise_for_status(resp, "connect")
        if not self.is_paper:
            self._require_long_only(resp.json())

    @staticmethod
    def _require_long_only(acct: dict) -> None:
        """Refuse a LIVE account that can short (2026-10-13, PASS 3 F-3).

        The system is long-only. A cancel/replace racing a fill at the broker could,
        on a shorting-enabled margin account, turn an over-sell into a real short; with
        shorting disabled the broker itself rejects it. Unknown field -> fail closed.
        """
        shorting = acct.get("shorting_enabled")
        if shorting is not False:
            raise BrokerRejectedError(
                "live Alpaca account must have shorting disabled (long-only system); "
                f"account reports shorting_enabled={shorting!r}. Disable shorting in the "
                "Alpaca account configuration (no_shorting) before trading live.")

    async def account_state(self) -> AccountState:
        acct_resp = await self._request("GET", "/v2/account", retry=True)
        self._raise_for_status(acct_resp, "account")
        acct = acct_resp.json()
        pos_resp = await self._request("GET", "/v2/positions", retry=True)
        self._raise_for_status(pos_resp, "positions")
        positions = tuple(
            Position(symbol=p["symbol"], qty=int(float(p["qty"])),
                     avg_price=float(p["avg_entry_price"]))
            for p in pos_resp.json()
        )
        settled = float(acct.get("non_marginable_buying_power", acct.get("cash", 0.0)))
        return AccountState(
            account_id=self.account_id,
            cash=float(acct.get("cash", 0.0)),
            settled_cash=settled,
            buying_power=float(acct.get("buying_power", 0.0)),
            positions=positions,
        )

    async def submit(self, intent: OrderIntent) -> OrderAck:
        if intent.order_type is OrderType.MARKET and intent.extended_hours:
            raise BrokerRejectedError("MARKET orders are not allowed in extended hours")

        existing = await self._lookup(intent.client_order_id)
        if existing is not None:
            return existing

        payload = self._order_payload(intent)
        result = await self._post_order(payload, intent.client_order_id)
        if isinstance(result, OrderAck):
            return result  # confirmed landed via lookup after a transient failure
        resp = result
        if resp.status_code == 422 and _DUP_ID_MSG in resp.text:
            found = await self._lookup(intent.client_order_id)
            if found is not None:
                return found
        self._raise_for_status(resp, "submit")
        return self._ack_from_order(resp.json())

    async def _post_order(self, payload: dict[str, Any], client_order_id: str) -> "httpx.Response | OrderAck":
        """POST with safe retry: on transport/5xx, confirm via lookup before retrying.

        Returns the raw response on a definitive outcome, or an OrderAck when a
        transient failure was followed by a lookup proving the order already landed
        (so we never double-submit a non-idempotent POST).
        """
        for attempt in range(_MAX_RETRIES):
            try:
                resp = await self._request("POST", "/v2/orders", retry=False, json=payload)
            except BrokerUnavailableError:
                landed = await self._lookup(client_order_id)
                if landed is not None:
                    return landed
                if attempt == _MAX_RETRIES - 1:
                    raise
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                landed = await self._lookup(client_order_id)
                if landed is not None:
                    return landed
                if attempt == _MAX_RETRIES - 1:
                    raise BrokerUnavailableError(f"submit: HTTP {resp.status_code}")
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                continue
            return resp
        raise BrokerUnavailableError("submit: retries exhausted")

    async def _lookup(self, client_order_id: str) -> OrderAck | None:
        resp = await self._request(
            "GET", "/v2/orders:by_client_order_id", retry=True,
            params={"client_order_id": client_order_id},
        )
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            if resp.status_code in (401, 403):
                self._raise_for_status(resp, "lookup")
            return None
        return self._ack_from_order(resp.json())

    async def cancel(self, client_order_id: str) -> OrderAck:
        ack = await self._lookup(client_order_id)
        if ack is None:
            return OrderAck(client_order_id, "", OrderStatus.CANCELED,
                            datetime.now(timezone.utc), "unknown order")
        if ack.status.terminal:
            return ack
        resp = await self._request("DELETE", f"/v2/orders/{ack.broker_order_id}", retry=True)
        if resp.status_code in (404, 422):
            return OrderAck(client_order_id, ack.broker_order_id, OrderStatus.CANCELED,
                            datetime.now(timezone.utc), "already terminal",
                            symbol=ack.symbol, side=ack.side, remaining_qty=0)
        self._raise_for_status(resp, "cancel")
        return OrderAck(client_order_id, ack.broker_order_id, OrderStatus.CANCELED,
                        datetime.now(timezone.utc), "canceled",
                        symbol=ack.symbol, side=ack.side, remaining_qty=0)

    async def open_orders(self) -> list[OrderAck]:
        resp = await self._request("GET", "/v2/orders", retry=True, params={"status": "open"})
        self._raise_for_status(resp, "open_orders")
        return [self._ack_from_order(o) for o in resp.json()]

    async def fills(self) -> AsyncIterator[Fill]:
        params: dict[str, Any] = {"direction": "asc"}
        if self._fill_cursor is not None:
            params["after"] = self._fill_cursor
        resp = await self._request("GET", "/v2/account/activities/FILL", retry=True, params=params)
        self._raise_for_status(resp, "fills")
        for act in resp.json():
            act_id = str(act["id"])
            if act_id in self._seen_fill_ids:
                continue
            self._seen_fill_ids.add(act_id)
            self._fill_cursor = act_id
            yield Fill(
                client_order_id=str(act.get("client_order_id", "")) or act_id,
                broker_order_id=str(act["order_id"]),
                account_id=self.account_id,
                symbol=act["symbol"],
                side=Side.BUY if str(act["side"]).lower().startswith("buy") else Side.SELL,
                qty=int(float(act["qty"])),
                price=float(act["price"]),
                ts=_parse_ts(act["transaction_time"]),
                fill_id=act_id,
            )

    async def health(self) -> AdapterHealth:
        try:
            resp = await self._request("GET", "/v2/account", retry=True)
        except BrokerUnavailableError as exc:
            return AdapterHealth(ok=False, detail=str(exc))
        if resp.status_code in (401, 403):
            return AdapterHealth(ok=False, detail="auth rejected")
        return AdapterHealth(ok=resp.status_code < 400, detail=f"HTTP {resp.status_code}")

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ mapping
    def _order_payload(self, intent: OrderIntent) -> dict[str, Any]:
        tif = {TimeInForce.DAY: "day", TimeInForce.IOC: "ioc", TimeInForce.GTC: "gtc"}[intent.tif]
        payload: dict[str, Any] = {
            "symbol": intent.symbol,
            "qty": str(intent.qty),
            "side": intent.side.value,
            "type": intent.order_type.value,
            "time_in_force": tif,
            "extended_hours": intent.extended_hours,
            "client_order_id": intent.client_order_id,
        }
        if intent.limit_price is not None:
            payload["limit_price"] = str(intent.limit_price)
        if intent.stop_price is not None:
            payload["stop_price"] = str(intent.stop_price)
        return payload

    def _ack_from_order(self, o: dict[str, Any]) -> OrderAck:
        status = _STATUS_MAP.get(str(o.get("status", "")).lower(), OrderStatus.PENDING)
        ts_raw = o.get("submitted_at") or o.get("created_at") or o.get("updated_at")
        ts = _parse_ts(ts_raw) if ts_raw else datetime.now(timezone.utc)
        side_raw = str(o.get("side", "")).lower()
        side = Side.BUY if side_raw.startswith("buy") else Side.SELL if side_raw else None
        remaining: int | None = None
        if "qty" in o:
            remaining = int(float(o.get("qty", 0))) - int(float(o.get("filled_qty", 0) or 0))
        return OrderAck(
            client_order_id=str(o.get("client_order_id", "")),
            broker_order_id=str(o.get("id", "")),
            status=status,
            ts=ts,
            message=str(o.get("status", "")),
            symbol=str(o.get("symbol", "")),
            side=side,
            remaining_qty=remaining,
        )


def _parse_ts(raw: str) -> datetime:
    s = raw.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
