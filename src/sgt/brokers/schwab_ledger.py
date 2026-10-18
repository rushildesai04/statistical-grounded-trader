"""Local idempotency ledger for the Schwab adapter (no broker-side client id).

Schwab's Trader API has no client-order-id field, so idempotency is enforced here
against ``order_ledger`` (keyed by ``client_order_id``). The adapter records an
intent as ``submitting`` before a POST and promotes the row to the real status with
the broker order id once a 201 lands; an ambiguous failure marks it ``unknown`` for
operator reconciliation. These statuses are local-only strings, distinct from the
``OrderStatus`` enum values the shared ledger otherwise stores.

Each call runs in its own short transaction via ``get_session`` so a crash can leave
at most a single row in a well-defined intermediate state.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from sgt.accounts.models import OrderLedger
from sgt.brokers.schwab_mapping import intent_to_json
from sgt.core.types import OrderAck, OrderIntent

SUBMITTING = "submitting"
UNKNOWN = "unknown"


def ledger_lookup(cid: str) -> tuple[str, str | None, dict[str, Any]] | None:
    """Return (status, broker_order_id|None, intent_json) for ``cid``, or None."""
    from sgt.db.session import get_session

    with get_session() as session:
        row = session.get(OrderLedger, cid)
        if row is None:
            return None
        return row.status, (row.broker_order_id or None), dict(row.intent or {})


def ledger_working_sells(account_id: str, symbol: str, exclude_cid: str) -> int:
    """Shares in local SELL rows that are in-flight (submitting/unknown, no broker id).

    These are orders a prior submit recorded locally but has not yet confirmed as a broker
    order id, so they are NOT visible in the broker's open-orders list. Counting them toward
    the long-only guard prevents a crash-interleaved pair of SELLs from overselling. Rows
    that already carry a broker id are excluded (they are counted via the broker's orders).
    """
    from sgt.db.session import get_session

    total = 0
    with get_session() as session:
        rows = session.scalars(
            select(OrderLedger).where(OrderLedger.account_id == account_id)).all()
        for row in rows:
            if row.client_order_id == exclude_cid or row.broker_order_id:
                continue
            if row.status not in (SUBMITTING, UNKNOWN):
                continue
            it = row.intent or {}
            if str(it.get("side", "")).lower() != "sell":
                continue
            if str(it.get("symbol", "")) != symbol:
                continue
            total += int(it.get("qty", 0) or 0)
    return total


def cid_for_order(order_id: str) -> str:
    """Resolve the client_order_id that produced a broker order id, or ''."""
    if not order_id:
        return ""
    from sgt.db.session import get_session

    with get_session() as session:
        row = session.scalars(
            select(OrderLedger).where(OrderLedger.broker_order_id == order_id)
        ).first()
        return row.client_order_id if row is not None else ""


def ledger_mark(intent: OrderIntent, status: str, broker_order_id: str | None,
                now: datetime) -> None:
    """Upsert the ledger row for ``intent`` to ``status`` (and broker id if known)."""
    from sgt.db.session import get_session

    with get_session() as session:
        row = session.get(OrderLedger, intent.client_order_id)
        if row is None:
            session.add(OrderLedger(
                client_order_id=intent.client_order_id,
                account_id=intent.account_id,
                broker_order_id=broker_order_id,
                status=status,
                intent=intent_to_json(intent),
                message="",
                submitted_at=intent.created_at,
                updated_at=now,
            ))
        else:
            row.status = status
            if broker_order_id:
                row.broker_order_id = broker_order_id
            row.updated_at = now
        session.flush()


def ledger_mark_from_ack(cid: str, ack: OrderAck, now: datetime) -> None:
    """Update an existing ledger row from a terminal/working ack (no-op if absent)."""
    from sgt.db.session import get_session

    with get_session() as session:
        row = session.get(OrderLedger, cid)
        if row is None:
            return
        row.status = ack.status.value
        if ack.broker_order_id:
            row.broker_order_id = ack.broker_order_id
        row.updated_at = now
        session.flush()
