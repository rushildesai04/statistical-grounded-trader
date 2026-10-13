"""Durable run record + heartbeat writes for the live runtime (plan §3.1, §14).

``live_sessions`` gets one row per run (created at startup, closed at shutdown).
``live_heartbeat`` gets one row per minute while the run is active so operators and
monitoring can confirm the process is alive and see which bar it last processed, how
many symbols it subscribes and how many positions are open. Every write is a short,
committed transaction; a heartbeat-write failure is logged and counted, never allowed
to crash the trading loop.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sgt.db.session import get_session
from sgt.live.models import LiveHeartbeat, LiveSession

log = logging.getLogger(__name__)


def open_session(run_id: str, mode: str, config_hash: str, started_at: datetime) -> None:
    with get_session() as s:
        row = s.get(LiveSession, run_id)
        if row is None:
            s.add(LiveSession(run_id=run_id, mode=mode, config_hash=config_hash,
                              started_at=started_at, ended_at=None, status="running"))
        else:
            row.mode = mode
            row.config_hash = config_hash
            row.started_at = started_at
            row.ended_at = None
            row.status = "running"


def close_session(run_id: str, status: str, ended_at: datetime) -> None:
    with get_session() as s:
        row = s.get(LiveSession, run_id)
        if row is not None:
            row.status = status
            row.ended_at = ended_at


def write_heartbeat(run_id: str, *, ts: datetime, last_bar_ts: datetime | None,
                    subscribed: int, open_positions: int) -> bool:
    """Append one heartbeat row. Returns False (logged) on a write failure."""
    try:
        with get_session() as s:
            s.add(LiveHeartbeat(run_id=run_id, ts=_utc(ts), last_bar_ts=_utc(last_bar_ts),
                               subscribed=int(subscribed), open_positions=int(open_positions)))
        return True
    except Exception as exc:  # bounded: monitoring write must not stop trading
        log.warning("heartbeat write failed for run %s: %s", run_id, type(exc).__name__)
        return False


def _utc(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
