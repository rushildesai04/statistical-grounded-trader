"""ORM tables owned by sgt.catalysts. Registered on the shared Base via sgt.db.registry.

All timestamps are timezone-aware UTC. Rows are append-only / idempotent-upsert;
nothing here is mutated in place, preserving point-in-time reconstruction.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from sgt.db.models import Base


class HaltEventRow(Base):
    """One trading halt/resume event. Idempotent on (symbol, ts, state)."""

    __tablename__ = "halt_events"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), primary_key=True)  # halted|resumed
    reason_code: Mapped[str] = mapped_column(String(16), default="")
    source: Mapped[str] = mapped_column(String(32), default="")


class SecFilingRow(Base):
    """One SEC EDGAR filing. ``accession`` is globally unique (primary key)."""

    __tablename__ = "sec_filings"
    accession: Mapped[str] = mapped_column(String(32), primary_key=True)
    cik: Mapped[int] = mapped_column(primary_key=False, index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    form: Mapped[str] = mapped_column(String(16), index=True)
    filed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class SharesSnapshotRow(Base):
    """One shares-outstanding observation (dei.EntityCommonStockSharesOutstanding).

    Keyed by (ticker, period_end, filed_date, source) so re-collection is idempotent
    and multiple filings reporting the same period are kept distinct by filing date.
    """

    __tablename__ = "shares_snapshots"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    filed_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    source: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[float] = mapped_column(Float)


class FloatOverrideRow(Base):
    """Manual free-float override, effective from ``effective_date`` (point-in-time)."""

    __tablename__ = "float_overrides"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    effective_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    free_float: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(48), default="manual")
    note: Mapped[str] = mapped_column(String(256), default="")
