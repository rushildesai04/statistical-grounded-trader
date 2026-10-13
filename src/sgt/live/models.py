"""ORM tables owned by sgt.live (plan §3.1, §14). Registered on the shared Base via
sgt.db.registry.

- ``live_sessions``  one row per live/shadow/paper run (run_id), its mode, config hash,
  start/end and final status — the durable record that a run happened.
- ``live_heartbeat`` one row per minute while a run is active; operators/monitoring read
  it to confirm the runtime is alive, which bar it last processed, how many symbols it
  subscribes and how many positions are open. A stale heartbeat is an outage signal.

All timestamps are timezone-aware UTC.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from sgt.db.models import Base


class LiveSession(Base):
    __tablename__ = "live_sessions"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    mode: Mapped[str] = mapped_column(String(16), index=True)
    config_hash: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="running", index=True)


class LiveHeartbeat(Base):
    __tablename__ = "live_heartbeat"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_bar_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    subscribed: Mapped[int] = mapped_column(Integer, default=0)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
