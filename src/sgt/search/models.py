"""ORM tables owned by sgt.search. Registered on the shared Base via sgt.db.registry.

Three tables back the honest-search lifecycle (plan §10, §11.3):

``strategy_registry``  one row per config hash: the versioned candidate card and its
                       lifecycle ``status`` (candidate -> validated -> shadow ->
                       champion -> retired). ``evidence`` holds the JSON acceptance /
                       benchmark record attached when the lockbox is opened.
``trials``             every config evaluated in a search run, with its params, OOS
                       metrics and trade count. ``(search_run_id, config_hash)`` is
                       unique so re-running a search is idempotent and the true trial
                       count (used by the Deflated Sharpe Ratio) is never inflated.
``lockbox_openings``   the one-time, logged lockbox evaluation per config. ``config_hash``
                       is unique: opening the lockbox a second time for the same config
                       raises rather than silently re-reading the held-out data.

All timestamps are tz-aware UTC.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sgt.db.models import Base

REGISTRY_STATUSES = ("candidate", "validated", "shadow", "champion", "retired")


class StrategyRegistry(Base):
    __tablename__ = "strategy_registry"

    config_hash: Mapped[str] = mapped_column(String(32), primary_key=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class Trial(Base):
    __tablename__ = "trials"
    __table_args__ = (
        UniqueConstraint("search_run_id", "config_hash", name="uq_trials_run_config"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    search_run_id: Mapped[str] = mapped_column(String(64), index=True)
    config_hash: Mapped[str] = mapped_column(String(32), index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    n_trades: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class LockboxOpening(Base):
    __tablename__ = "lockbox_openings"
    __table_args__ = (
        UniqueConstraint("config_hash", name="uq_lockbox_config"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    config_hash: Mapped[str] = mapped_column(String(32), index=True)
    search_run_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
