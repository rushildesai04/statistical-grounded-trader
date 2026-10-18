"""Local idempotency ledger helpers for the SnapTrade adapter.

SnapTrade's ``place_force_order`` accepts a ``client_order_id`` but most brokerages neither
honour nor echo it, so idempotency is enforced here against ``order_ledger`` (keyed by
``client_order_id``). Each call runs in its own short ``get_session`` transaction so a crash
leaves at most a single row in a well-defined intermediate state. Kept apart from
``snaptrade.py`` so the adapter module stays within the size budget and the DB access is
isolated and independently testable.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from sgt.accounts.ledger import _intent_json, record_ack
from sgt.accounts.models import BrokerFill, OrderLedger
from sgt.core.types import OrderAck, OrderIntent, OrderStatus
from sgt.db.session import get_session

# Local ledger statuses that still represent shares potentially working at the broker but
# not yet confirmed against a broker order id (counts toward the long-only guard).
NON_TERMINAL = ("pending", "submitting", "unknown", "working")


def ledger_row(cid: str) -> dict[str, Any] | None:
    with get_session() as session:
        row = session.get(OrderLedger, cid)
        if row is None:
            return None
        return {"broker_order_id": row.broker_order_id or "",
                "status": row.status, "intent": dict(row.intent or {})}


def ledger_register(intent: OrderIntent) -> bool:
    """Atomically pre-register an intent as PENDING with no broker id.

    Returns False when any writer (this or another process) already holds the row; the
    caller must then take the existing-row path (reconcile), never POST (R2-D)."""
    from sgt.brokers.submit_guard import claim_ledger_row

    return claim_ledger_row(intent, OrderStatus.PENDING.value, _intent_json(intent),
                            datetime.now(timezone.utc))


def ledger_set_status(cid: str, status: str, message: str) -> None:
    with get_session() as session:
        row = session.get(OrderLedger, cid)
        if row is None:
            return
        row.status = status
        row.message = message
        row.updated_at = datetime.now(timezone.utc)
        session.flush()


def ledger_working_sells(account_id: str, symbol: str, exclude_cid: str) -> int:
    """Shares in local SELL rows that are non-terminal and lack a broker id.

    A broker id means the order is (or was) live at the broker and is already counted via
    ``open_orders``; rows without one are in-flight submits not yet visible there.
    """
    total = 0
    with get_session() as session:
        rows = session.scalars(
            select(OrderLedger).where(OrderLedger.account_id == account_id)).all()
        for row in rows:
            if row.client_order_id == exclude_cid or row.broker_order_id:
                continue
            if row.status not in NON_TERMINAL:
                continue
            it = row.intent or {}
            if str(it.get("side", "")).lower() != "sell":
                continue
            if str(it.get("symbol", "")).upper() != symbol.upper():
                continue
            total += int(it.get("qty", 0) or 0)
    return total


def ledger_by_broker_id(account_id: str, broker_order_id: str) -> dict[str, Any] | None:
    """Ledger row for a broker order id in this account (restores tracking after restart)."""
    if not broker_order_id:
        return None
    with get_session() as session:
        row = session.scalars(
            select(OrderLedger).where(OrderLedger.account_id == account_id,
                                      OrderLedger.broker_order_id == broker_order_id)).first()
        if row is None:
            return None
        return {"cid": row.client_order_id, "status": row.status,
                "intent": dict(row.intent or {})}


def recorded_fill_qty(account_id: str, broker_order_id: str) -> int:
    """Shares already durably recorded in broker_fills for this order (R2-B).

    SnapTrade exposes only a cumulative ``filled_quantity`` per order; deltas must be
    computed against what was already persisted, not against process memory, or a
    restart re-emits the whole cumulative quantity as a new fill."""
    with get_session() as session:
        total = session.execute(
            select(func.coalesce(func.sum(BrokerFill.qty), 0)).where(
                BrokerFill.account_id == account_id,
                BrokerFill.broker_order_id == broker_order_id)).scalar_one()
    return int(total or 0)
