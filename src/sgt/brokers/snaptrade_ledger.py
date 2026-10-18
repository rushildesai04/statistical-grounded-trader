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

from sqlalchemy import select

from sgt.accounts.ledger import record_ack
from sgt.accounts.models import OrderLedger
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


def ledger_register(intent: OrderIntent) -> None:
    """Pre-register an intent as PENDING with no broker id (idempotent)."""
    ack = OrderAck(intent.client_order_id, "", OrderStatus.PENDING,
                   intent.created_at, "pre-register", symbol=intent.symbol,
                   side=intent.side, remaining_qty=intent.qty)
    with get_session() as session:
        if session.get(OrderLedger, intent.client_order_id) is None:
            record_ack(session, ack, intent)


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
