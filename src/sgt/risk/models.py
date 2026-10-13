"""ORM tables owned by sgt.risk. Registered on the shared Base via sgt.db.registry.

All timestamps are tz-aware UTC (DateTime(timezone=True)). Two tables are
append-only audit trails (``loss_ledger``, ``risk_audit``); the other two hold
mutable control state (``risk_switch``, ``strategy_status``).

Every row is scoped by ``namespace`` so the engine's modes never share state:
LIVE ("live"), PAPER ("paper"), SHADOW ("shadow") and each BACKTEST run (an explicit
run id) get independent switches, per-strategy status, ledgers and audit trails. This
is the safety boundary that stops a backtest/shadow/paper loss from consuming the
real-money learning budget or corrupting live control state (F-1).

The loss ledger records one row per fill (not only realized-PnL sells) so that
(namespace, fill_id) uniqueness gives idempotency for ``RiskManager.on_fill`` and the
manager can replay the ledger on restart to rebuild avg-cost positions and budget
totals. Buys carry ``realized_pnl == 0`` (their fees are absorbed into cost basis).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sgt.db.models import Base


class RiskSwitch(Base):
    """Master arming switch, one row per ``namespace``.

    Default and post-restart state is DISARMED (see RiskStore.load(fresh_start=True)).
    """

    __tablename__ = "risk_switch"

    namespace: Mapped[str] = mapped_column(String(48), primary_key=True)
    armed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    armed_by: Mapped[str] = mapped_column(String(64), default="")
    armed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str] = mapped_column(String(256), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class StrategyStatus(Base):
    """Per-strategy enablement, capital-ladder stage and size multiplier, per namespace."""

    __tablename__ = "strategy_status"

    namespace: Mapped[str] = mapped_column(String(48), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    stage: Mapped[str] = mapped_column(String(16), default="shadow", nullable=False)
    size_multiplier: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    disabled_reason: Mapped[str] = mapped_column(String(256), default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LossLedger(Base):
    """Append-only journal of every risk-relevant fill. (namespace, fill_id) is unique."""

    __tablename__ = "loss_ledger"
    __table_args__ = (UniqueConstraint("namespace", "fill_id", name="uq_loss_ledger_ns_fill_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(48), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    strategy_id: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fill_id: Mapped[str] = mapped_column(String(128))


class RiskAudit(Base):
    """Append-only audit trail: every non-APPROVE verdict and every control change."""

    __tablename__ = "risk_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    namespace: Mapped[str] = mapped_column(String(48), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
