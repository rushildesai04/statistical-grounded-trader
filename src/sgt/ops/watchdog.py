"""Session-hours watchdog for the listener (operational risk review, 2026-11-23).

The listener writes a heartbeat on every pass of the short book (every 10 s, around the clock). Until now only two
things read it: the 09:20 ET pre-market check and the 20:50 ET summary. A listener that hung or kept crashing at
11:00 was therefore silent until the evening: no stop check for open paper positions, no alert read, and nothing told
the owner. This runs from the per-minute control job, a separate process, so it works exactly when the listener
does not: in alert hours a heartbeat older than STALE is texted once, and again every AGAIN while it lasts.

A failure notice, not a trade text: zero on a normal day, at most CAP a day.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from sgt.ops import text_guard
from sgt.ops.store import OpsStore, iso

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
STALE = timedelta(minutes=3)            # 18 missed stop checks; a deploy never falls in alert hours
AGAIN = timedelta(minutes=30)
CAP, EVENT, FLAG = 6, "watchdog_text", "watchdog_last"


def check(db: OpsStore, send: Callable[[str], None], now: datetime, in_hours=None) -> str | None:
    """Text the owner when the listener's heartbeat is stale in alert hours. Returns the text sent, else None.
    Never raises (it rides on the control job, which must go on)."""
    try:
        if in_hours is None:
            from sgt.fast import chan_poll

            in_hours = chan_poll.active
        if not in_hours(now):
            return None
        beat = db.flag("fade_heartbeat")
        if not beat:
            return None                                     # never ran: the pre-market check says so
        seen = datetime.fromisoformat(beat)
        if now - seen <= STALE:
            return None
        last = db.flag(FLAG)
        if last and now - datetime.fromisoformat(last) < AGAIN:
            return None
        with db.read() as c:
            shorts = c.execute("SELECT COUNT(*) FROM fade_trades WHERE status IN ('ask','pending','open')").fetchone()[0]
            longs = c.execute("SELECT COUNT(*) FROM long_trades WHERE status='open'").fetchone()[0]
        held = (f"Open on paper: {shorts} short, {longs} long. Their stops are NOT being checked."
                if shorts or longs else "Nothing is open.")
        text = (f"[SGT] ⚠️ The listener has not run for {int((now - seen).total_seconds() // 60)} min "
                f"(last pass {seen.astimezone(ET):%H:%M} ET). Alerts are not being read. {held} "
                "It restarts by itself after a crash; if this text repeats, it is stuck.")
        db.set_flag(FLAG, iso(now))
        text_guard.send_capped(db, send, EVENT, CAP, text, "listener watchdog", now)
        return text
    except Exception:  # noqa: BLE001 - a watchdog that breaks the control job would be worse than none
        log.exception("listener watchdog failed")
        return None
