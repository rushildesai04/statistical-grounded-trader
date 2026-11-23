"""Session-hours watchdog for the listener (operational risk review, 2026-11-23).

The listener writes a heartbeat on every pass of the short book (every 10 s, around the clock). Until now only two
things read it: the 09:20 ET pre-market check and the 20:50 ET summary. A listener that hung or kept crashing at
11:00 was therefore silent until the evening: no stop check for open paper positions, no alert read, and nothing told
the owner. This runs from the per-minute control job, a separate process, so it works exactly when the listener
does not: in alert hours a heartbeat older than STALE is texted once, and again every AGAIN while it lasts.

What "silent" means: NOT "no alerts arrived". Three signs of life, each written by the listener whether or not the
channel posts anything: the short book's stop check (the heartbeat flag, every 10 s around the clock), the long
book's stop check and the reading of the alert channel (liveness files, ``own_thread.beat``: every 15 s in alert
hours). A hung event loop stops all three; a stuck book thread stops its own; a dead Telegram connection stops the
third while the stops still run, and the text says which.

What it cannot see: a machine that is down takes this job down with it. That needs a check from outside the machine.

A failure notice, not a trade text: zero on a normal day, at most CAP a day.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from sgt.ops import text_guard
from sgt.ops.store import OpsStore, iso

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
STALE = timedelta(minutes=3)            # 18 missed stop checks; a deploy never falls in alert hours
AGAIN = timedelta(minutes=30)
CAP, EVENT, FLAG = 6, "watchdog_text", "watchdog_last"


def silent(db: OpsStore, now: datetime) -> dict[str, float]:
    """The parts of the listener that show no sign of life: name -> seconds since the last one. A part that never
    left a sign (a file that does not exist yet) is not reported: unknown is not silent."""
    from sgt.fast import chan_poll
    from sgt.ops import own_thread

    out: dict[str, float] = {}
    beat = db.flag("fade_heartbeat")
    if beat:
        age = (now - datetime.fromisoformat(beat)).total_seconds()
        if age > STALE.total_seconds():
            out["short book"] = age
    folder, at = Path(db.path).parent, now.timestamp()
    age = own_thread.beat_age(folder, "long book", at)
    if age is not None and age > STALE.total_seconds():
        out["long book"] = age
    every = chan_poll.interval(db.flag(chan_poll.FLAG)) or 300        # the fast check switched off: one pass in 5 min
    age = own_thread.beat_age(folder, "alerts", at)
    if age is not None and age > max(STALE.total_seconds(), 3 * every):
        out["alerts"] = age
    return out


def check(db: OpsStore, send: Callable[[str], None], now: datetime, in_hours=None) -> str | None:
    """Text the owner when the listener's heartbeat is stale in alert hours. Returns the text sent, else None.
    Never raises (it rides on the control job, which must go on)."""
    try:
        if in_hours is None:
            from sgt.fast import chan_poll

            in_hours = chan_poll.active
        if not in_hours(now):
            return None
        quiet = silent(db, now)
        if not quiet:
            return None
        last = db.flag(FLAG)
        if last and now - datetime.fromisoformat(last) < AGAIN:
            return None
        with db.read() as c:
            shorts = c.execute("SELECT COUNT(*) FROM fade_trades WHERE status IN ('ask','pending','open')").fetchone()[0]
            longs = c.execute("SELECT COUNT(*) FROM long_trades WHERE status='open'").fetchone()[0]
        mins = lambda s: int(s // 60)  # noqa: E731
        if "short book" in quiet:                           # the pass that also drives everything the owner sees
            seen = now - timedelta(seconds=quiet["short book"])
            held = (f"Open on paper: {shorts} short, {longs} long. Their stops are NOT being checked."
                    if shorts or longs else "Nothing is open.")
            read = "Alerts are not being read. " if "alerts" in quiet or len(quiet) == 1 else ""
            text = (f"[SGT] ⚠️ The listener has not run for {mins(quiet['short book'])} min "
                    f"(last pass {seen.astimezone(ET):%H:%M} ET). {read}{held} "
                    "It restarts by itself after a crash; if this text repeats, it is stuck.")
        else:
            parts = []
            if "alerts" in quiet:
                parts.append(f"the alert channel has not been read for {mins(quiet['alerts'])} min: new alerts are "
                             "NOT reaching the books")
            if "long book" in quiet:
                parts.append(f"the long book has not checked its positions for {mins(quiet['long book'])} min"
                             + (f" ({longs} open on paper)" if longs else ""))
            text = ("[SGT] ⚠️ Listener problem: " + "; ".join(parts) + ". The short book's stop checks are running. "
                    "If this text repeats, it is stuck.")
        how = text_guard.send_capped(db, send, EVENT, CAP, text, "listener watchdog", now)
        if how == "failed":
            return None                                     # not sent: no mark, so the next minute tries again
        db.set_flag(FLAG, iso(now))                         # sent, or over the day's limit: quiet for AGAIN
        return text if how == "sent" else None
    except Exception:  # noqa: BLE001 - a watchdog that breaks the control job would be worse than none
        log.exception("listener watchdog failed")
        return None
