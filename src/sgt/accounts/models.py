"""ORM tables owned by sgt.accounts. Registered on the shared Base via sgt.db.registry.

- ``accounts``       one row per connected brokerage account (registry + limits).
- ``order_ledger``   one row per client_order_id — the idempotency + audit ledger.
- ``broker_fills``   one row per execution (fill_id) — the execution audit trail.
- ``manual_fill_inbox`` operator-reported executions for alert-only accounts, pending
  consumption by the owning ``AlertOnlyBroker`` (control-plane -> broker hand-off).

All timestamps are timezone-aware UTC.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sgt.db.models import Base


class Account(Base):
    __tablename__ = "accounts"
    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(8), default="cash")  # cash|margin|ira
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    allocation_weight: Mapped[float] = mapped_column(Float, default=1.0)
    max_notional: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class OrderLedger(Base):
    __tablename__ = "order_ledger"
    client_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    intent: Mapped[dict] = mapped_column(JSON, default=dict)
    message: Mapped[str] = mapped_column(Text, default="")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class BrokerFill(Base):
    __tablename__ = "broker_fills"
    fill_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    broker_order_id: Mapped[str] = mapped_column(String(64), default="")
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ManualFillInbox(Base):
    """One operator-reported execution for an alert-only account, awaiting consumption.

    The control plane writes a row here when an operator records a manual fill; the
    owning ``AlertOnlyBroker.poll_inbox`` consumes unconsumed rows for its account,
    marking them consumed in the same transaction so a row is handed to the broker
    exactly once. Rows are retained after consumption as an audit trail.
    """

    __tablename__ = "manual_fill_inbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    client_order_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    recorded_by: Mapped[str] = mapped_column(String(64))
    consumed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
