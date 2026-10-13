"""Alert-only broker: never executes, pushes a human-readable instruction instead.

The universal fallback for any account without a compliant trading API (Robinhood and
similar). ``submit`` sends an instruction through a ``Notifier`` and returns a PENDING
ack with message ``"manual"``; the order is only real once the operator reports an
execution through ``record_manual_fill`` (validated), which then flows through
``fills()`` like any other adapter.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Protocol, runtime_checkable

import httpx

from sgt.accounts.ledger import ManualFillRecord, consume_manual_fills
from sgt.config import get_settings
from sgt.core.interfaces import AdapterHealth, BrokerAdapter, BrokerUnavailableError
from sgt.core.types import (
    AccountState,
    Fill,
    OrderAck,
    OrderIntent,
    OrderStatus,
    Position,
    Side,
)
from sgt.db.session import get_session

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PollResult:
    """Outcome of one ``AlertOnlyBroker.poll_inbox`` drain.

    ``filled`` are the executions accepted into the broker (also surfaced via ``fills()``);
    ``rejected`` pairs each inbox record that failed validation with its reason. Rejected
    rows are already marked consumed (never re-handed) and are logged, never dropped.
    """

    filled: list[Fill] = field(default_factory=list)
    rejected: list[tuple[ManualFillRecord, str]] = field(default_factory=list)


@runtime_checkable
class Notifier(Protocol):
    def send(self, title: str, body: str) -> None: ...


class LogNotifier:
    """Writes the instruction to the application log (default, dependency-free)."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or log

    def send(self, title: str, body: str) -> None:
        self._log.info("ALERT %s: %s", title, body)


class WebhookNotifier:
    """POSTs the instruction as JSON to ``settings.alert_webhook_url`` (bounded)."""

    def __init__(self, url: str | None = None, timeout_s: float | None = None) -> None:
        settings = get_settings()
        self._url = url or settings.alert_webhook_url
        self._timeout = timeout_s if timeout_s is not None else settings.http_timeout_s
        if not self._url:
            raise ValueError("WebhookNotifier requires alert_webhook_url")

    def send(self, title: str, body: str) -> None:
        try:
            resp = httpx.post(
                self._url, json={"title": title, "body": body},
                timeout=httpx.Timeout(self._timeout),
            )
        except httpx.HTTPError as exc:
            raise BrokerUnavailableError(f"alert webhook failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise BrokerUnavailableError(f"alert webhook HTTP {resp.status_code}")


class AlertOnlyBroker(BrokerAdapter):
    name = "alert_only"
    supports_extended_hours = True
    supports_broker_side_stops = False
    is_paper = False

    def __init__(self, account_id: str, notifier: Notifier) -> None:
        self.account_id = account_id
        self._notifier = notifier
        self._acks: dict[str, OrderAck] = {}
        self._intents: dict[str, OrderIntent] = {}
        self._fills: list[Fill] = []
        self._fill_ids: set[str] = set()
        self._emitted: set[str] = set()
        self._positions: dict[str, tuple[int, float]] = {}

    async def connect(self) -> None:
        return None

    async def account_state(self) -> AccountState:
        positions = tuple(
            Position(sym, qty, round(avg, 6))
            for sym, (qty, avg) in sorted(self._positions.items()) if qty != 0
        )
        return AccountState(self.account_id, 0.0, 0.0, 0.0, positions)

    async def submit(self, intent: OrderIntent) -> OrderAck:
        existing = self._acks.get(intent.client_order_id)
        if existing is not None:
            return existing
        self._notifier.send(
            title=f"MANUAL ORDER {self.account_id}",
            body=self._instruction(intent),
        )
        ack = OrderAck(intent.client_order_id, "", OrderStatus.PENDING,
                       intent.created_at, "manual",
                       symbol=intent.symbol, side=intent.side, remaining_qty=intent.qty)
        self._acks[intent.client_order_id] = ack
        self._intents[intent.client_order_id] = intent
        return ack

    async def cancel(self, client_order_id: str) -> OrderAck:
        intent = self._intents.get(client_order_id)
        if intent is not None:
            self._notifier.send(
                title=f"CANCEL MANUAL ORDER {self.account_id}",
                body=f"Cancel {intent.side.value} {intent.qty} {intent.symbol} (id {client_order_id})",
            )
        ack = OrderAck(client_order_id, "", OrderStatus.CANCELED,
                       datetime.now(timezone.utc), "manual cancel",
                       symbol=intent.symbol if intent else "",
                       side=intent.side if intent else None, remaining_qty=0)
        self._acks[client_order_id] = ack
        return ack

    async def open_orders(self) -> list[OrderAck]:
        return [a for a in self._acks.values() if not a.status.terminal]

    async def fills(self) -> AsyncIterator[Fill]:
        for fl in list(self._fills):
            if fl.fill_id in self._emitted:
                continue
            self._emitted.add(fl.fill_id)
            yield fl

    async def health(self) -> AdapterHealth:
        return AdapterHealth(ok=True, detail="alert-only")

    async def close(self) -> None:
        return None

    # ------------------------------------------------------------------ manual fills
    def record_manual_fill(
        self,
        client_order_id: str,
        symbol: str,
        side: Side,
        qty: int,
        price: float,
        ts: datetime,
        *,
        fill_id: str | None = None,
        fee: float = 0.0,
    ) -> Fill:
        """Record an operator-reported execution for a previously submitted order."""
        intent = self._intents.get(client_order_id)
        if intent is None:
            raise ValueError(f"no submitted order for client_order_id {client_order_id!r}")
        if symbol != intent.symbol or side is not intent.side:
            raise ValueError("manual fill symbol/side must match the submitted order")
        if qty <= 0 or price <= 0:
            raise ValueError("manual fill qty/price must be > 0")
        already = sum(f.qty for f in self._fills if f.client_order_id == client_order_id)
        if already + qty > intent.qty:
            raise ValueError("manual fills exceed ordered qty")
        fid = fill_id or f"{client_order_id}-m{already + qty}"
        if fid in self._fill_ids:
            raise ValueError(f"duplicate manual fill id {fid!r}")
        fill = Fill(
            client_order_id=client_order_id,
            broker_order_id="",
            account_id=self.account_id,
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            ts=ts,
            fill_id=fid,
            fee=fee,
        )
        self._fill_ids.add(fid)
        self._fills.append(fill)
        self._apply(fill)
        total = already + qty
        self._acks[client_order_id] = OrderAck(
            client_order_id, "", OrderStatus.FILLED if total >= intent.qty
            else OrderStatus.PARTIALLY_FILLED, ts, "manual fill",
            symbol=intent.symbol, side=intent.side, remaining_qty=intent.qty - total)
        return fill

    def poll_inbox(self) -> PollResult:
        """Drain this account's manual-fill inbox and route each row through validation.

        Rows are consumed durably first (their own transaction), then applied in memory
        via ``record_manual_fill``. A row that fails validation (no matching order, qty
        overfill, bad values) is recorded in ``PollResult.rejected`` and logged with the
        reason — never silently dropped, and never re-handed (it stays consumed).
        """
        with get_session() as session:
            records = consume_manual_fills(session, self.account_id)
        result = PollResult()
        for rec in records:
            try:
                fill = self.record_manual_fill(
                    rec.client_order_id,
                    rec.symbol,
                    Side(rec.side),
                    rec.qty,
                    rec.price,
                    rec.executed_at,
                    fill_id=f"inbox-{rec.id}",
                )
            except ValueError as exc:
                reason = str(exc)
                result.rejected.append((rec, reason))
                log.warning(
                    "manual-fill inbox row %s (%s %s %s) rejected for account %s: %s",
                    rec.id, rec.side, rec.qty, rec.symbol, self.account_id, reason,
                )
                continue
            result.filled.append(fill)
        return result

    def _apply(self, fill: Fill) -> None:
        qty, avg = self._positions.get(fill.symbol, (0, 0.0))
        if fill.side is Side.BUY:
            new_qty = qty + fill.qty
            avg = (qty * avg + fill.qty * fill.price) / new_qty if new_qty else 0.0
            qty = new_qty
        else:
            qty -= fill.qty
            if qty == 0:
                avg = 0.0
        self._positions[fill.symbol] = (qty, avg)

    @staticmethod
    def _instruction(intent: OrderIntent) -> str:
        parts = [intent.side.value.upper(), str(intent.qty), intent.symbol,
                 intent.order_type.value.upper()]
        if intent.limit_price is not None:
            parts.append(f"@ {intent.limit_price}")
        if intent.stop_price is not None:
            parts.append(f"stop {intent.stop_price}")
        parts.append(f"[{intent.tif.value}]")
        if intent.extended_hours:
            parts.append("(ext-hours)")
        parts.append(f"reason={intent.reason}")
        parts.append(f"id={intent.client_order_id}")
        return " ".join(parts)
