"""ORM tables owned by sgt.engine (trade + candidate journals, plan §11.1).

Registered on the shared Base via sgt.db.registry. Backtests do NOT write these by
default (they are opt-in via ``JournalWriter``); live/paper/shadow persist every
trade and every candidate — taken or skipped — so "why did it (not) trade?" is
always answerable from the database. Every row is scoped by ``run_id`` and ``mode``
so runs never collide and a backtest run can be dropped wholesale.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from sgt.db.models import Base


class TradeJournal(Base):
    __tablename__ = "trade_journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    config_hash: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    entry_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    qty: Mapped[int] = mapped_column(Integer)
    pnl: Mapped[float] = mapped_column(Float, default=0.0)
    ret: Mapped[float] = mapped_column(Float, default=0.0)
    exit_reason: Mapped[str] = mapped_column(String(48), default="")
    mfe: Mapped[float] = mapped_column(Float, default=0.0)
    mae: Mapped[float] = mapped_column(Float, default=0.0)
    bars_held: Mapped[int] = mapped_column(Integer, default=0)
    halted_during: Mapped[bool] = mapped_column(Boolean, default=False)
    # Realized vs modeled slippage for the trade (plan §11.4). Nullable + additive: existing
    # rows and backtest trades (which do not populate them) stay NULL, and the slippage-drift
    # monitor is only rehydrated from rows that carry both (2026-10-13, L-B slippage gap).
    realized_slippage: Mapped[float | None] = mapped_column(Float, nullable=True)
    modeled_slippage: Mapped[float | None] = mapped_column(Float, nullable=True)


class CandidateJournal(Base):
    __tablename__ = "candidate_journal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    config_hash: Mapped[str] = mapped_column(String(32), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    taken: Mapped[bool] = mapped_column(Boolean, index=True)
    skip_reason: Mapped[str] = mapped_column(String(32), default="")
    features: Mapped[dict] = mapped_column(JSON, default=dict)
