"""Charles Schwab individual Trader API v1 adapter (LIVE only — no sandbox).

Places REAL orders. Correctness and fail-closed behavior dominate every design
choice here. Schwab's Trader API has NO client-order-id field and NO sandbox, so:

- Idempotency is enforced LOCALLY via ``order_ledger`` (keyed by client_order_id);
  see ``sgt.brokers.schwab_ledger``. Before a POST we record the intent as
  ``submitting``; after a 201 we store the broker order id. A second submit of the
  same client_order_id returns the stored ack and never issues a second POST.
- A POST that times out / 5xx is NEVER blindly retried (non-idempotent, no dedupe
  key). We list orders entered in the last few minutes and match by
  (symbol, side, qty, price, time window). We resubmit only when the order is
  confidently absent (zero matches); an ambiguous result (more than one match, or a
  failed resubmit) marks the ledger row ``unknown`` and raises
  ``BrokerUnavailableError`` for an operator/reconciler to resolve.
- Long-only is enforced client-side: a SELL whose quantity exceeds the broker-
  reported long position is rejected before submission (Schwab exposes no simple
  shorting-disabled flag).

Credentials: ``sgt.brokers.schwab_auth`` (keychain ``schwab_app_key`` /
``schwab_app_secret`` / ``schwab_refresh_token`` / ``schwab_refresh_issued_at``).
JSON<->core-type mapping lives in ``sgt.brokers.schwab_mapping``.

[VERIFY] against official Schwab developer docs before enabling a live account —
third-party-sourced facts that MUST be confirmed:
- Base host ``https://api.schwabapi.com``; all Trader endpoints under ``/trader/v1``.
- ``GET /trader/v1/accounts/accountNumbers`` -> ``[{accountNumber, hashValue}]``;
  every other account call uses the opaque ``hashValue``.
- ``GET /trader/v1/accounts/{hash}?fields=positions`` -> ``securitiesAccount`` with
  ``currentBalances.cashBalance`` / ``.buyingPower`` / ``.cashAvailableForTrading``
  and ``positions[]`` of ``{instrument.symbol, longQuantity, averagePrice}``.
- ``POST /trader/v1/accounts/{hash}/orders`` -> 201 with the new order id in the
  ``Location`` header; body uses ``orderType``, ``session``, ``duration``,
  ``price``/``stopPrice``, ``orderStrategyType: SINGLE`` and one
  ``orderLegCollection`` leg (see ``schwab_mapping.order_body``).
- ``GET /trader/v1/accounts/{hash}/orders?fromEnteredTime&toEnteredTime`` -> orders
  with ``status``, ``filledQuantity``, ``remainingQuantity``, ``enteredTime`` and
  ``orderActivityCollection[].executionLegs[]`` of ``{legId, price, quantity, time}``;
  entered-time query params are ISO-8601 ``...THH:MM:SS.sssZ``.
- ``DELETE /trader/v1/accounts/{hash}/orders/{orderId}`` cancels a working order.
- Duration/status mappings and ``assetType: EQUITY`` in ``schwab_mapping``.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable

import httpx

from sgt.brokers import schwab_ledger as ledger
from sgt.brokers import schwab_mapping as mapping
from sgt.brokers.schwab_auth import SCHWAB_BASE_URL, SchwabTokenManager
from sgt.brokers.schwab_ledger import SUBMITTING as _SUBMITTING
from sgt.brokers.schwab_ledger import UNKNOWN as _UNKNOWN
from sgt.config import get_settings
from sgt.core.interfaces import (
    AdapterHealth,
    BrokerAdapter,
    BrokerAuthError,
    BrokerRejectedError,
    BrokerUnavailableError,
)
from sgt.core.types import (
    AccountState,
    Fill,
    OrderAck,
    OrderIntent,
    OrderStatus,
    OrderType,
    Segment,
    Side,
)

log = logging.getLogger(__name__)

_MAX_RETRIES = 3
_BACKOFF_BASE_S = 0.25
_MATCH_SLACK_MIN = 5          # slack subtracted from the intent anchor when reconciling
# Schwab's order-history query is bounded; an intent older than this cannot be reliably
# reconciled, so a failed POST that old is marked UNKNOWN rather than resubmitted.
# [VERIFY] the exact maximum query span against the Schwab Trader API docs.
_MAX_QUERY_WINDOW = timedelta(days=60)
_HEALTH_REAUTH_MARGIN = timedelta(hours=24)


class SchwabBroker(BrokerAdapter):
    name = "schwab"
    supports_extended_hours = True
    supports_broker_side_stops = True  # RTH only; STOP orders are never extended-hours
    is_paper = False                   # no Schwab sandbox exists

    def __init__(
        self,
        account_id: str,
        account_number_last4: str,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
        auth: SchwabTokenManager | None = None,
        segment_fn: Callable[[datetime], Segment | None] = mapping.segment_for,
    ) -> None:
        last4 = str(account_number_last4)
        if len(last4) != 4 or not last4.isdigit():
            raise ValueError("account_number_last4 must be the 4 trailing digits")
        self.account_id = account_id
        self._last4 = last4
        self._timeout = get_settings().http_timeout_s
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._segment_fn = segment_fn
        self._owns_client = client is None
        self._client = client
        # Single-writer token management: all adapters in this process sharing an app
        # credential use ONE SchwabTokenManager (process registry + asyncio lock), and a
        # cross-process advisory file lock serializes the single-use refresh-token rotation.
        self._auth = auth or SchwabTokenManager.shared(clock=self._clock)
        self._account_hash: str | None = None
        self._seen_fill_ids: set[str] = set()

    def _now(self) -> datetime:
        return self._clock()

    # ------------------------------------------------------------------ transport
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=SCHWAB_BASE_URL, timeout=httpx.Timeout(self._timeout)
            )
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        retry: bool,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """One bounded HTTP call with a bearer token, mapped errors and 401 refresh.

        Retries 429/5xx/transport with backoff ONLY when ``retry`` (idempotent GETs).
        A 401 forces a single token refresh and one retry (a 401 means the call did
        not take effect, so retrying even a POST after it is safe).
        """
        client = self._http()
        refreshed = False
        last_exc: Exception | None = None
        attempts = _MAX_RETRIES if retry else 1
        attempt = 0
        while attempt < attempts:
            token = await self._auth.access_token(force=False)
            headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
            if json is not None:
                headers["Content-Type"] = "application/json"
            try:
                resp = await client.request(method, path, json=json, params=params,
                                            headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                log.warning("schwab %s %s transport error: %s",
                            method, path, type(exc).__name__)
                if not retry or attempt == attempts - 1:
                    raise BrokerUnavailableError(
                        f"{method} {path}: {type(exc).__name__}") from exc
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                attempt += 1
                continue
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                await self._auth.access_token(force=True)  # raises BrokerAuthError on failure
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last_exc = BrokerUnavailableError(f"{method} {path}: HTTP {resp.status_code}")
                if not retry or attempt == attempts - 1:
                    raise last_exc
                await asyncio.sleep(_BACKOFF_BASE_S * (2 ** attempt))
                attempt += 1
                continue
            return resp
        assert last_exc is not None
        raise last_exc

    def _raise_for_status(self, resp: httpx.Response, context: str) -> None:
        code = resp.status_code
        if code < 400:
            return
        body = resp.text[:500]
        if code == 401:
            raise BrokerAuthError(f"{context}: HTTP 401")
        if code in (400, 403, 422):
            raise BrokerRejectedError(f"{context}: HTTP {code} {body}")
        if code == 429 or code >= 500:
            raise BrokerUnavailableError(f"{context}: HTTP {code}")
        raise BrokerRejectedError(f"{context}: HTTP {code} {body}")

    def _accounts_path(self, suffix: str = "") -> str:
        if self._account_hash is None:
            raise BrokerUnavailableError("not connected: account hash unresolved")
        return f"/trader/v1/accounts/{self._account_hash}{suffix}"

    def _orders_window(self, minutes_back: int) -> dict[str, str]:
        now = self._now()
        return {
            "fromEnteredTime": mapping.fmt_entered(now - timedelta(minutes=minutes_back)),
            "toEnteredTime": mapping.fmt_entered(now + timedelta(minutes=1)),
        }

    # ------------------------------------------------------------------ adapter API
    async def connect(self) -> None:
        resp = await self._request("GET", "/trader/v1/accounts/accountNumbers", retry=True)
        self._raise_for_status(resp, "connect")
        entries = resp.json()
        if not isinstance(entries, list):
            raise BrokerUnavailableError("connect: unexpected accountNumbers payload")
        matches = [
            e for e in entries
            if str(e.get("accountNumber", "")).endswith(self._last4) and e.get("hashValue")
        ]
        if len(matches) != 1:
            raise BrokerRejectedError(
                f"account hash resolution: expected exactly 1 account ending {self._last4}, "
                f"found {len(matches)} (fail closed)")
        self._account_hash = str(matches[0]["hashValue"])
        log.info("schwab connected: account %s resolved to a hash", self.account_id)

    async def account_state(self) -> AccountState:
        resp = await self._request("GET", self._accounts_path(), retry=True,
                                   params={"fields": "positions"})
        self._raise_for_status(resp, "account")
        return mapping.account_state_from(self.account_id, resp.json())

    async def submit(self, intent: OrderIntent) -> OrderAck:
        if intent.order_type is OrderType.MARKET and intent.extended_hours:
            raise BrokerRejectedError("MARKET orders are not allowed in extended hours")

        prior = ledger.ledger_lookup(intent.client_order_id)
        if prior is not None:
            status, broker_order_id, stored = prior
            if status == _UNKNOWN:
                raise BrokerUnavailableError(
                    f"prior submit of {intent.client_order_id} has an UNKNOWN outcome; "
                    "operator reconciliation required before resubmitting")
            if broker_order_id:
                return self._ack_known(intent, broker_order_id)
            # Row is 'submitting' with no broker id: a prior attempt crashed before
            # confirming. Reconcile against the broker (anchored on the ORIGINAL intent's
            # created_at, not now) rather than blindly resubmit.
            return await self._reconcile_after_failure(
                intent, mapping.anchor_from_stored(stored, intent.created_at))

        if intent.side is Side.SELL:
            await self._enforce_long_only(intent)
        ledger.ledger_mark(intent, _SUBMITTING, None, self._now())
        return await self._post_order(intent)

    async def _enforce_long_only(self, intent: OrderIntent) -> None:
        """Refuse a SELL exceeding the AVAILABLE long position (no shorts).

        Available = broker-reported long position minus shares already working in OTHER SELL
        orders for the symbol: open SELLs at the broker plus in-flight local ledger rows
        (submitting/unknown, no broker id). Mirrors the IBKR guard so a full-size STOP plus a
        full-size exit SELL cannot oversell.
        """
        state = await self.account_state()
        held = next((p.qty for p in state.positions if p.symbol == intent.symbol), 0)
        working = await self._working_sell_qty(intent.symbol, intent.client_order_id)
        available = held - working
        if intent.qty > available:
            raise BrokerRejectedError(
                f"long-only: SELL {intent.qty} {intent.symbol} exceeds available long "
                f"position {available} (held={held}, working_sells={working}); "
                "refusing to open or increase a short")

    async def _working_sell_qty(self, symbol: str, exclude_cid: str) -> int:
        resp = await self._request("GET", self._accounts_path("/orders"), retry=True,
                                   params=self._orders_window(24 * 60))
        self._raise_for_status(resp, "working-sells")
        total = 0
        for o in resp.json():
            ack = mapping.ack_from_order("", o, self._now())
            if (ack.status in mapping.OPEN_STATUSES and ack.side is Side.SELL
                    and ack.symbol == symbol):
                total += int(ack.remaining_qty or 0)
        return total + ledger.ledger_working_sells(self.account_id, symbol, exclude_cid)

    def _body(self, intent: OrderIntent) -> dict[str, Any]:
        return mapping.order_body(
            intent, mapping.session_for(intent, self._now(), self._segment_fn))

    async def _post_order(self, intent: OrderIntent) -> OrderAck:
        try:
            resp = await self._request("POST", self._accounts_path("/orders"),
                                       retry=False, json=self._body(intent))
        except BrokerUnavailableError:
            return await self._reconcile_after_failure(intent, intent.created_at)
        if resp.status_code == 201:
            return self._ack_from_location(intent, resp)
        self._raise_for_status(resp, "submit")  # 4xx -> Rejected; order not placed
        raise BrokerUnavailableError("submit: unexpected response")

    async def _reconcile_after_failure(self, intent: OrderIntent, anchor: datetime) -> OrderAck:
        """Detect whether a failed/unknown POST actually landed, matching by fields.

        The order-history query is anchored on ``anchor`` (the intent's created_at), not on
        now, so a stuck row from minutes-to-days ago is still covered. If the anchor predates
        the broker's queryable window the outcome cannot be determined -> mark UNKNOWN and
        raise; never resubmit blindly.
        """
        if self._now() - anchor > _MAX_QUERY_WINDOW:
            ledger.ledger_mark(intent, _UNKNOWN, None, self._now())
            raise BrokerUnavailableError(
                f"submit of {intent.client_order_id} cannot be reconciled: the intent is "
                f"older than the Schwab order-query window ({_MAX_QUERY_WINDOW.days}d); "
                "operator reconciliation required")
        matches = await self._find_recent_matches(intent, anchor)
        if len(matches) == 1:
            ack = mapping.ack_from_order(intent.client_order_id, matches[0], self._now())
            ledger.ledger_mark(intent, ack.status.value, ack.broker_order_id, self._now())
            return ack
        if len(matches) == 0:
            # Confidently absent -> exactly one safe resubmit.
            try:
                resp = await self._request("POST", self._accounts_path("/orders"),
                                           retry=False, json=self._body(intent))
            except BrokerUnavailableError as exc:
                ledger.ledger_mark(intent, _UNKNOWN, None, self._now())
                raise BrokerUnavailableError(
                    f"resubmit of {intent.client_order_id} failed; outcome UNKNOWN; "
                    "operator reconciliation required") from exc
            if resp.status_code == 201:
                return self._ack_from_location(intent, resp)
            self._raise_for_status(resp, "resubmit")
            raise BrokerUnavailableError("resubmit: unexpected response")
        # Ambiguous (>1 candidate) -> never guess.
        ledger.ledger_mark(intent, _UNKNOWN, None, self._now())
        raise BrokerUnavailableError(
            f"submit outcome ambiguous for {intent.client_order_id} "
            f"({len(matches)} candidate orders); operator reconciliation required")

    async def _find_recent_matches(self, intent: OrderIntent,
                                   anchor: datetime) -> list[dict[str, Any]]:
        window = mapping.reconcile_window(anchor, self._now(), _MATCH_SLACK_MIN)
        resp = await self._request("GET", self._accounts_path("/orders"), retry=True,
                                   params=window)
        self._raise_for_status(resp, "order-match")
        return [o for o in resp.json() if mapping.order_matches(o, intent)]

    async def cancel(self, client_order_id: str) -> OrderAck:
        prior = ledger.ledger_lookup(client_order_id)
        if prior is None or not prior[1]:
            return OrderAck(client_order_id, "", OrderStatus.CANCELED,
                            self._now(), "unknown order")
        broker_order_id = prior[1]
        resp = await self._request("DELETE", self._accounts_path(f"/orders/{broker_order_id}"),
                                   retry=False)
        if resp.status_code in (200, 201, 204, 404, 400):
            ack = OrderAck(client_order_id, broker_order_id, OrderStatus.CANCELED,
                           self._now(), "canceled", remaining_qty=0)
            ledger.ledger_mark_from_ack(client_order_id, ack, self._now())
            return ack
        self._raise_for_status(resp, "cancel")
        raise BrokerUnavailableError("cancel: unexpected response")

    async def open_orders(self) -> list[OrderAck]:
        resp = await self._request("GET", self._accounts_path("/orders"), retry=True,
                                   params=self._orders_window(24 * 60))
        self._raise_for_status(resp, "open_orders")
        out: list[OrderAck] = []
        for o in resp.json():
            ack = mapping.ack_from_order(ledger.cid_for_order(str(o.get("orderId", ""))),
                                         o, self._now())
            if ack.status in mapping.OPEN_STATUSES:
                out.append(ack)
        return out

    async def fills(self) -> AsyncIterator[Fill]:
        resp = await self._request("GET", self._accounts_path("/orders"), retry=True,
                                   params=self._orders_window(24 * 60))
        self._raise_for_status(resp, "fills")
        for order in resp.json():
            order_id = str(order.get("orderId", ""))
            cid = ledger.cid_for_order(order_id) or order_id
            for fill in mapping.fills_from_order(order, self.account_id, cid):
                if fill.fill_id in self._seen_fill_ids:
                    continue
                self._seen_fill_ids.add(fill.fill_id)
                yield fill

    async def health(self) -> AdapterHealth:
        expires_at = self._auth.refresh_expires_at()
        if expires_at is None:
            return AdapterHealth(
                ok=False,
                detail="no Schwab refresh token / issued_at; re-authorization required",
                auth_expires_at=None)
        remaining = expires_at - self._now()
        if remaining < _HEALTH_REAUTH_MARGIN:
            return AdapterHealth(
                ok=False,
                detail=f"Schwab re-auth due by {expires_at.isoformat()} "
                       f"({remaining.total_seconds() / 3600:.1f}h left)",
                auth_expires_at=expires_at)
        return AdapterHealth(ok=True, detail=f"auth valid until {expires_at.isoformat()}",
                             auth_expires_at=expires_at)

    async def close(self) -> None:
        await self._auth.close()
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ------------------------------------------------------------------ ack helpers
    def _ack_from_location(self, intent: OrderIntent, resp: httpx.Response) -> OrderAck:
        location = resp.headers.get("Location", "")
        broker_order_id = location.rstrip("/").rsplit("/", 1)[-1] if location else ""
        if not broker_order_id:
            # 201 without a usable Location means we can't track the order -> fail closed.
            ledger.ledger_mark(intent, _UNKNOWN, None, self._now())
            raise BrokerUnavailableError(
                "submit: 201 without a parseable Location order id; outcome UNKNOWN")
        ack = mapping.working_ack(intent, broker_order_id, self._now(), "submitted")
        ledger.ledger_mark(intent, ack.status.value, broker_order_id, self._now())
        return ack

    def _ack_known(self, intent: OrderIntent, broker_order_id: str) -> OrderAck:
        return mapping.working_ack(intent, broker_order_id, self._now(), "already submitted")
