"""SnapTrade multi-broker adapter (trade-capable and read-only connections).

SnapTrade (https://snaptrade.com) brokers a single signed API across many brokerages.
Some connections are *trade-enabled* (Webull, E*TRADE, tastytrade, Public, moomoo, ...)
and accept order placement; others are *read-only* (notably Robinhood, which has no
official equities trading API — we deliberately do NOT use unofficial clients). Read-only
connections feed reconciliation/exposure and pair with ``AlertOnlyBroker`` for manual
execution. ``SnapTradeBroker`` fails closed: if the connection is read-only or the adapter
was not explicitly trade-enabled, ``submit``/``cancel`` raise ``BrokerRejectedError`` and
never touch the network.

Idempotency without reliable broker client-order-ids: SnapTrade's ``place_force_order``
accepts an optional ``client_order_id`` but most brokerages neither honour nor echo it, so
we do not trust it. Instead every submit pre-registers a row in ``order_ledger`` keyed by
``client_order_id``; a resubmit returns the recorded ack, and a submit whose POST outcome
is unknown (timeout/5xx) reconciles against recent orders by (symbol, side, qty, price,
time window) BEFORE any resubmit — a unique match is adopted, an ambiguous result marks the
ledger ``unknown`` and raises ``BrokerUnavailableError`` for manual resolution.

Endpoints/fields are mapped in ``snaptrade_mapping.py``; the signing scheme lives in
``snaptrade_client.py``. [VERIFY] list (confirmed 2026-10-17 from the official
``snaptrade-python-sdk`` v13 README + typed models + ``docs/request-signatures.md``):
- Base ``https://api.snaptrade.com/api/v1``; auth = partner ``clientId``+``consumerKey`` +
  HMAC-SHA256 ``Signature`` header + Unix ``timestamp`` query; user scope via
  ``userId``/``userSecret`` query. [CONFIRMED: request-signatures.md, test_signing.py]
- ``POST /snapTrade/registerUser`` -> ``{userSecret}``; ``POST /snapTrade/login`` ->
  ``{redirectURI}``; ``GET /accounts/all``; ``GET /authorizations`` and
  ``GET /authorizations/{id}`` (``type`` in {read,trade}, ``disabled``). [CONFIRMED: README, models]
- ``GET /accounts/{id}/positions/all``, ``GET /accounts/{id}/balances``,
  ``GET /accounts/{id}/orders`` (``state``,``days``) -> ``AccountOrderRecord``. [CONFIRMED: models]
- ``POST /trade/place`` body ``account_id``/``action``/``order_type``/``time_in_force``/
  ``symbol``/``units``/``price``/``stop``/``trading_session``/``client_order_id``.
  [CONFIRMED: type/manual_trade_form_with_options.py]
- ``POST /accounts/{id}/trading/cancel`` body ``{"brokerage_order_id"}``.
  [VERIFY: JSON key assumed — SDK types the body as ``Any``; the ``brokerage_order_id`` param is confirmed.]
- Rate limits: customer 250/min, account 10/min; SnapTrade recommends <=1 trade req/s/account —
  serialized through a 1/s per-account limiter. [CONFIRMED: README "Rate limiting"]
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from sgt.accounts.ledger import record_ack, record_fill
from sgt.config import get_settings
from sgt.core.interfaces import (
    AdapterHealth,
    BrokerAdapter,
    BrokerAuthError,
    BrokerRejectedError,
    BrokerUnavailableError,
)
from sgt.core.secrets import get_secret
from sgt.core.types import (
    AccountState,
    Fill,
    OrderAck,
    OrderIntent,
    OrderStatus,
    Position,
    Side,
)
from sgt.brokers import snaptrade_ledger as sledger
from sgt.brokers import snaptrade_mapping as smap
from sgt.brokers.snaptrade_client import SnapTradeClient
from sgt.brokers.snaptrade_mapping import (
    account_state_from,
    num,
    parse_ts,
    positions_from_json,
)
from sgt.db.session import get_session

__all__ = [
    "SnapTradeBroker",
    "ReadOnlyPositionSource",
    "account_state_from",
    "positions_from_json",
]

log = logging.getLogger(__name__)

_LEDGER_UNKNOWN = "unknown"
_CANCEL_POLL_ATTEMPTS = 3       # confirm a cancel landed before marking the ledger terminal
_CANCEL_POLL_INTERVAL_S = 0.3

# --------------------------------------------------------------------------- adapter
class SnapTradeBroker(BrokerAdapter):
    name = "snaptrade"
    supports_extended_hours = False  # trading_session mapping is brokerage-specific; fail closed
    supports_broker_side_stops = True
    is_paper = False

    def __init__(
        self,
        account_id: str,
        snaptrade_account_id: str,
        *,
        trade_enabled: bool,
        authorization_id: str | None = None,
        client: SnapTradeClient | None = None,
    ) -> None:
        self.account_id = account_id
        self._st_account_id = snaptrade_account_id
        self._trade_enabled = trade_enabled
        self._authorization_id = authorization_id
        self._verified_trade = False
        if client is None:
            settings = get_settings()
            client = SnapTradeClient(
                client_id=get_secret("snaptrade_client_id") or "",
                consumer_key=get_secret("snaptrade_consumer_key") or "",
                user_id=get_secret("snaptrade_user_id") or "",
                user_secret=get_secret("snaptrade_user_secret") or "",
                timeout_s=settings.http_timeout_s,
            )
        self._client = client
        self._tracked: dict[str, dict[str, Any]] = {}   # brokerage_order_id -> {cid, symbol, side}
        self._emitted_filled: dict[str, float] = {}      # brokerage_order_id -> cumulative filled qty
        self._seen_fill_ids: set[str] = set()

    # ------------------------------------------------------------------ lifecycle
    async def connect(self) -> None:
        if not self._trade_enabled:
            return  # read-only adapter: account_state/positions only, never verify trade
        auth = await self._resolve_authorization()
        conn_type = str(auth.get("type", "")).lower()
        disabled = bool(auth.get("disabled", False))
        if conn_type != "trade" or disabled:
            raise BrokerRejectedError(
                "trade_enabled=True but SnapTrade connection is not trade-capable "
                f"(type={conn_type!r}, disabled={disabled}): use alert-only execution")
        self._verified_trade = True

    async def _resolve_authorization(self) -> dict[str, Any]:
        if self._authorization_id:
            return await self._client.get_authorization(self._authorization_id)
        for acct in await self._client.list_accounts():
            if str(acct.get("id")) == self._st_account_id:
                auth_id = str(acct.get("brokerage_authorization", ""))
                if auth_id:
                    self._authorization_id = auth_id
                    return await self._client.get_authorization(auth_id)
        raise BrokerRejectedError(
            f"SnapTrade account {self._st_account_id!r} not found among linked accounts")

    def _require_trade(self) -> None:
        if not self._trade_enabled or not self._verified_trade:
            raise BrokerRejectedError("read-only connection: use alert-only execution")

    # ------------------------------------------------------------------ reads
    async def account_state(self) -> AccountState:
        balances = await self._client.get_balances(self._st_account_id)
        positions = await self._client.get_positions(self._st_account_id)
        return account_state_from(self.account_id, balances, positions)

    async def open_orders(self) -> list[OrderAck]:
        orders = await self._client.get_orders(self._st_account_id, state="open", days=7)
        acks: list[OrderAck] = []
        for rec in orders:
            ack = smap.ack_from_record(rec, self._cid_for(rec))
            if not ack.status.terminal:
                acks.append(ack)
        return acks

    async def fills(self) -> AsyncIterator[Fill]:
        orders = await self._client.get_orders(self._st_account_id, state="all", days=7)
        for rec in orders:
            boid = str(rec.get("brokerage_order_id", ""))
            track = self._tracked.get(boid)
            if track is None:
                continue
            filled = num(rec.get("filled_quantity"))
            prev = self._emitted_filled.get(boid, 0.0)
            if filled <= prev:
                continue
            qty = int(round(filled - prev))
            price = num(rec.get("execution_price"))
            if qty <= 0 or price <= 0:
                continue
            fill_id = f"{boid}:{int(round(filled))}"
            if fill_id in self._seen_fill_ids:
                continue
            fill = Fill(
                client_order_id=str(track["cid"]),
                broker_order_id=boid,
                account_id=self.account_id,
                symbol=str(track["symbol"]),
                side=track["side"],
                qty=qty,
                price=price,
                ts=parse_ts(rec.get("time_executed") or rec.get("time_updated") or rec.get("time_placed")),
                fill_id=fill_id,
            )
            self._seen_fill_ids.add(fill_id)
            self._emitted_filled[boid] = filled
            with get_session() as session:
                record_fill(session, fill)
            yield fill

    # ------------------------------------------------------------------ writes
    async def submit(self, intent: OrderIntent) -> OrderAck:
        self._require_trade()
        if intent.extended_hours:
            raise BrokerRejectedError("extended-hours orders are not supported on SnapTrade connections")

        existing = sledger.ledger_row(intent.client_order_id)
        if existing is not None:
            if existing["status"] == _LEDGER_UNKNOWN:
                raise BrokerUnavailableError(
                    f"prior submit outcome for {intent.client_order_id!r} is unknown; resolve manually")
            if existing["broker_order_id"] and existing["status"] not in ("rejected",):
                return smap.ack_from_ledger(intent, existing)
            if not existing["broker_order_id"] and existing["status"] not in ("rejected",):
                # Crash-restart: a prior attempt pre-registered (or was mid-POST) but never
                # recorded a broker id. Reconcile against the broker BEFORE any POST — anchored
                # on the ORIGINAL intent's created_at — rather than placing a duplicate order.
                anchor = smap.anchor_from_stored(existing, intent.created_at)
                return await self._reconcile_or_resubmit(intent, anchor)

        if intent.side is Side.SELL:
            await self._reject_oversell(intent)

        sledger.ledger_register(intent)
        return await self._place_with_reconcile(intent)

    async def _place_with_reconcile(self, intent: OrderIntent) -> OrderAck:
        try:
            rec = await self._client.place_force_order(
                smap.place_body(intent, self._st_account_id))
        except (BrokerAuthError, BrokerRejectedError) as exc:
            sledger.ledger_set_status(intent.client_order_id, "rejected", str(exc)[:200])
            raise
        except BrokerUnavailableError:
            return await self._reconcile_or_resubmit(intent, intent.created_at)
        return self._adopt(intent, rec)

    async def _reconcile_or_resubmit(self, intent: OrderIntent, anchor: datetime) -> OrderAck:
        """Reconcile a failed/unconfirmed POST, then resubmit at most once on a zero-match.

        Unique match -> adopt it (the order landed). Ambiguous (>1) -> mark ``unknown`` and
        raise (never guess). Confident zero-match -> exactly ONE resubmit; a second failure
        marks ``unknown`` and raises (no resubmit loop), matching the Schwab adapter.
        """
        matches = await self._reconcile(intent, anchor)
        if len(matches) == 1:
            return self._adopt(intent, matches[0])
        if len(matches) > 1:
            sledger.ledger_set_status(intent.client_order_id, _LEDGER_UNKNOWN,
                                      "ambiguous reconciliation match")
            raise BrokerUnavailableError(
                f"submit {intent.client_order_id!r}: ambiguous recent-order match; ledger=unknown")
        # Zero matches: the order is confidently absent. Re-check long-only (a resubmit is a
        # brand-new order) and place exactly once.
        if intent.side is Side.SELL:
            await self._reject_oversell(intent)
        try:
            rec = await self._client.place_force_order(
                smap.place_body(intent, self._st_account_id))
        except (BrokerAuthError, BrokerRejectedError) as exc:
            sledger.ledger_set_status(intent.client_order_id, "rejected", str(exc)[:200])
            raise
        except BrokerUnavailableError as exc:
            sledger.ledger_set_status(intent.client_order_id, _LEDGER_UNKNOWN,
                                      "resubmit failed; outcome unknown")
            raise BrokerUnavailableError(
                f"resubmit of {intent.client_order_id!r} failed; outcome UNKNOWN; "
                "operator reconciliation required") from exc
        return self._adopt(intent, rec)

    def _adopt(self, intent: OrderIntent, rec: dict[str, Any]) -> OrderAck:
        ack = smap.ack_from_record(rec, intent.client_order_id)
        boid = ack.broker_order_id
        if boid:
            self._tracked[boid] = {"cid": intent.client_order_id,
                                   "symbol": intent.symbol, "side": intent.side}
        with get_session() as session:
            record_ack(session, ack, intent)
        return ack

    async def cancel(self, client_order_id: str) -> OrderAck:
        self._require_trade()
        row = sledger.ledger_row(client_order_id)
        if row is None or not row["broker_order_id"]:
            return OrderAck(client_order_id, "", OrderStatus.CANCELED,
                            datetime.now(timezone.utc), "unknown order")
        boid = row["broker_order_id"]
        intent = row["intent"]
        if row["status"] in ("filled", "canceled", "rejected", "expired"):
            return OrderAck(client_order_id, boid, OrderStatus(row["status"]),
                            datetime.now(timezone.utc), "already terminal",
                            symbol=str(intent.get("symbol", "")), remaining_qty=0)
        await self._client.cancel_order(self._st_account_id, boid)
        side = Side(intent["side"]) if intent.get("side") else None
        symbol = str(intent.get("symbol", ""))
        # SnapTrade's cancel endpoint only ACKs the request; most brokerages cancel
        # asynchronously. Poll order status and mark the ledger terminal ONLY once the
        # order is observed canceled/terminal — otherwise leave it working so a fill that
        # races the cancel is still reconciled rather than silently dropped.
        observed = await self._poll_cancel_terminal(boid)
        if observed is not None and observed.status.terminal:
            sledger.ledger_set_status(client_order_id, observed.status.value, "canceled")
            return OrderAck(client_order_id, boid, observed.status,
                            datetime.now(timezone.utc), "canceled",
                            symbol=symbol or observed.symbol, side=side or observed.side,
                            remaining_qty=0)
        remaining = observed.remaining_qty if observed is not None else None
        return OrderAck(client_order_id, boid, OrderStatus.WORKING,
                        datetime.now(timezone.utc),
                        "cancel requested; not yet confirmed terminal",
                        symbol=symbol, side=side, remaining_qty=remaining)

    async def _poll_cancel_terminal(self, boid: str) -> OrderAck | None:
        """Return the latest ack for ``boid`` once terminal, else the last non-terminal ack.

        Bounded poll of the orders list; ``None`` when the order id is not present at all.
        """
        last: OrderAck | None = None
        for attempt in range(_CANCEL_POLL_ATTEMPTS):
            orders = await self._client.get_orders(self._st_account_id, state="all", days=1)
            rec = next((o for o in orders if str(o.get("brokerage_order_id", "")) == boid), None)
            if rec is not None:
                last = smap.ack_from_record(rec, self._cid_for(rec))
                if last.status.terminal:
                    return last
            if attempt < _CANCEL_POLL_ATTEMPTS - 1:
                await asyncio.sleep(_CANCEL_POLL_INTERVAL_S)
        return last

    async def health(self) -> AdapterHealth:
        try:
            await self._client.call("GET", "/", user_scoped=False)
        except BrokerAuthError as exc:
            return AdapterHealth(ok=False, detail=str(exc))
        except BrokerUnavailableError as exc:
            return AdapterHealth(ok=False, detail=str(exc))
        return AdapterHealth(ok=True, detail="ok")

    async def close(self) -> None:
        await self._client.close()

    # ------------------------------------------------------------------ helpers
    async def _reject_oversell(self, intent: OrderIntent) -> None:
        """Refuse a SELL exceeding the AVAILABLE long position (no shorts).

        Available = held shares minus shares already working in OTHER SELL orders for the
        symbol: broker-reported open SELLs plus local ledger rows that are non-terminal and
        not yet mapped to a broker id (in-flight submits). Mirrors the IBKR guard so a
        full-size STOP plus a full-size exit SELL cannot oversell the position.
        """
        state = await self.account_state()
        symbol = intent.symbol.upper()
        held = next((p.qty for p in state.positions if p.symbol == symbol), 0)
        working = await self._working_sell_qty(symbol, exclude_cid=intent.client_order_id)
        available = held - working
        if intent.qty > available:
            raise BrokerRejectedError(
                f"long-only: SELL {intent.qty} {intent.symbol} exceeds available long "
                f"position {available} (held={held}, working_sells={working})")

    async def _working_sell_qty(self, symbol: str, *, exclude_cid: str) -> int:
        total = 0
        for ack in await self.open_orders():
            if ack.side is Side.SELL and ack.symbol.upper() == symbol:
                total += int(ack.remaining_qty or 0)
        return total + sledger.ledger_working_sells(self.account_id, symbol, exclude_cid)

    async def _reconcile(self, intent: OrderIntent, anchor: datetime) -> list[dict[str, Any]]:
        orders = await self._client.get_orders(self._st_account_id, state="all", days=1)
        return [o for o in orders if smap.order_matches(o, intent, anchor)]

    def _cid_for(self, rec: dict[str, Any]) -> str:
        track = self._tracked.get(str(rec.get("brokerage_order_id", "")))
        return str(track["cid"]) if track else ""


# --------------------------------------------------------------------------- read-only source
class ReadOnlyPositionSource:
    """Positions + cash snapshot for a read-only SnapTrade account (e.g. Robinhood).

    Duck-types the slice of ``BrokerAdapter`` the reconciler uses (``account_id`` +
    ``account_state``) so ``sgt.accounts.reconcile.Reconciler.run`` can drive it directly,
    while never exposing order placement. Use this for brokerages SnapTrade lists as
    read-only; execution for those accounts goes through ``AlertOnlyBroker``.
    """

    def __init__(self, account_id: str, snaptrade_account_id: str, *, client: SnapTradeClient) -> None:
        self.account_id = account_id
        self._st_account_id = snaptrade_account_id
        self._client = client

    async def account_state(self) -> AccountState:
        balances = await self._client.get_balances(self._st_account_id)
        positions = await self._client.get_positions(self._st_account_id)
        return account_state_from(self.account_id, balances, positions)

    async def positions(self) -> tuple[Position, ...]:
        return (await self.account_state()).positions

    async def cash(self) -> float:
        return (await self.account_state()).cash

    async def close(self) -> None:
        await self._client.close()
