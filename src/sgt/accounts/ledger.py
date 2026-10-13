"""Idempotent persistence of order acks and fills to the audit ledger.

The ledger is the source of truth for idempotency (an order_ledger row keyed by
client_order_id proves whether we already submitted) and for reconciliation (summing
broker_fills gives our view of positions). Both writers are idempotent: re-applying the
same ack or fill is a no-op or a monotonic status update, never a duplicate row.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from sgt.accounts.models import BrokerFill, ManualFillInbox, OrderLedger
from sgt.core.types import Fill, OrderAck, OrderIntent, Side
from sgt.util import to_utc

# Terminal statuses never regress to a non-terminal one (protects against
# out-of-order ack delivery, e.g. a stale WORKING arriving after FILLED).
_TERMINAL = {"filled", "canceled", "rejected", "expired"}


def _intent_json(intent: OrderIntent) -> dict:
    d = asdict(intent)
    for k, v in list(d.items()):
        if isinstance(v, datetime):
            d[k] = v.isoformat()
        elif hasattr(v, "value"):
            d[k] = v.value
    return d


def record_ack(session: Session, ack: OrderAck, intent: OrderIntent) -> None:
    """Upsert an order_ledger row for an ack. Idempotent; never regresses a terminal."""
    row = session.get(OrderLedger, ack.client_order_id)
    now = datetime.now(timezone.utc)
    if row is None:
        session.add(OrderLedger(
            client_order_id=ack.client_order_id,
            account_id=intent.account_id,
            broker_order_id=ack.broker_order_id or None,
            status=ack.status.value,
            intent=_intent_json(intent),
            message=ack.message,
            submitted_at=intent.created_at,
            updated_at=now,
        ))
        session.flush()
        return
    if row.status in _TERMINAL and ack.status.value not in _TERMINAL:
        return  # do not regress a terminal order
    row.status = ack.status.value
    if ack.broker_order_id:
        row.broker_order_id = ack.broker_order_id
    row.message = ack.message
    row.updated_at = now
    session.flush()


def record_fill(session: Session, fill: Fill) -> bool:
    """Insert a broker_fills row. Returns False if the fill_id already exists."""
    if session.get(BrokerFill, fill.fill_id) is not None:
        return False
    session.add(BrokerFill(
        fill_id=fill.fill_id,
        client_order_id=fill.client_order_id,
        account_id=fill.account_id,
        broker_order_id=fill.broker_order_id,
        symbol=fill.symbol,
        side=fill.side.value,
        qty=fill.qty,
        price=fill.price,
        fee=fill.fee,
        ts=fill.ts,
    ))
    session.flush()
    return True


@dataclass(frozen=True, slots=True)
class ManualFillRecord:
    """Immutable snapshot of one inbox row, safe to use after the row is mutated."""

    id: int
    account_id: str
    client_order_id: str
    symbol: str
    side: str
    qty: int
    price: float
    executed_at: datetime
    recorded_by: str


def enqueue_manual_fill(
    session: Session,
    *,
    account_id: str,
    client_order_id: str,
    symbol: str,
    side: Side,
    qty: int,
    price: float,
    executed_at: datetime,
    recorded_by: str,
) -> int:
    """Queue an operator-reported execution for later consumption by the broker.

    Validates at the boundary (this is operator-entered data). Returns the new row id.
    """
    if not account_id:
        raise ValueError("account_id required")
    if not client_order_id:
        raise ValueError("client_order_id required")
    sym = symbol.upper()
    if not sym:
        raise ValueError("symbol required")
    if not isinstance(side, Side):
        raise ValueError("side must be a Side")
    if qty <= 0 or price <= 0:
        raise ValueError("qty and price must be > 0")
    if executed_at.tzinfo is None or executed_at.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError("executed_at must be tz-aware UTC")
    if not recorded_by:
        raise ValueError("recorded_by required")
    row = ManualFillInbox(
        account_id=account_id,
        client_order_id=client_order_id,
        symbol=sym,
        side=side.value,
        qty=qty,
        price=price,
        executed_at=executed_at,
        recorded_by=recorded_by,
        consumed=False,
        created_at=datetime.now(timezone.utc),
    )
    session.add(row)
    session.flush()
    return int(row.id)


def consume_manual_fills(session: Session, account_id: str) -> list[ManualFillRecord]:
    """Return unconsumed inbox rows for ``account_id`` and mark them consumed atomically.

    The read and the consumed-flag update happen in the caller's single transaction, so
    a row is handed out exactly once even under concurrent pollers.
    """
    rows = session.scalars(
        select(ManualFillInbox)
        .where(ManualFillInbox.account_id == account_id, ManualFillInbox.consumed.is_(False))
        .order_by(ManualFillInbox.id)
    ).all()
    out = [
        ManualFillRecord(
            id=int(r.id),
            account_id=r.account_id,
            client_order_id=r.client_order_id,
            symbol=r.symbol,
            side=r.side,
            qty=r.qty,
            price=r.price,
            executed_at=to_utc(r.executed_at),
            recorded_by=r.recorded_by,
        )
        for r in rows
    ]
    for r in rows:
        r.consumed = True
    session.flush()
    return out


def ledger_positions(session: Session, account_id: str) -> dict[str, int]:
    """Net signed position per symbol for an account, summed from broker_fills."""
    rows = session.scalars(
        select(BrokerFill).where(BrokerFill.account_id == account_id)
    ).all()
    out: dict[str, int] = {}
    for r in rows:
        delta = r.qty if r.side == Side.BUY.value else -r.qty
        out[r.symbol] = out.get(r.symbol, 0) + delta
    return {s: q for s, q in out.items() if q != 0}
