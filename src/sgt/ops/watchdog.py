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
from datetime import datetime, timedelta, timezone
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


MARK = "watchdog.mark"                 # beside the database: its modification time = when the last warning went out


def _last_warned(db: OpsStore) -> datetime:
    """When the last warning was sent: the later of the flag and the marker file. The file exists because the flag
    cannot be written exactly when it matters most: with a full disk every database write fails, the text still
    goes out, and without a second memory the warning would repeat every minute, uncapped (found 2026-11-23)."""
    out = datetime.fromtimestamp(0, tz=timezone.utc)
    try:
        v = db.flag(FLAG)
        if v:
            out = max(out, datetime.fromisoformat(v))
    except Exception:  # noqa: BLE001 - an unreadable database: the file still answers
        pass
    try:
        out = max(out, datetime.fromtimestamp((Path(db.path).parent / MARK).stat().st_mtime, tz=timezone.utc))
    except OSError:
        pass
    return out


def _mark_warned(db: OpsStore, now: datetime) -> None:
    try:
        db.set_flag(FLAG, iso(now))
    except Exception:  # noqa: BLE001 - the file below is the fallback
        log.exception("listener watchdog: flag not written")
    try:                                                    # changing a file's time needs no free space
        import os

        os.utime(Path(db.path).parent / MARK, (now.timestamp(), now.timestamp()))
    except OSError:
        log.exception("listener watchdog: marker not updated")


def ready(db: OpsStore) -> None:
    """Create the marker while the disk is healthy (called on every run; creating it later may be impossible)."""
    try:
        p = Path(db.path).parent / MARK
        if not p.exists():
            import os

            p.touch()
            os.utime(p, (0, 0))                             # never warned yet
    except OSError:
        log.exception("listener watchdog: marker not created")


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
    # how often the channel is read: the fast check in alert hours (0 = switched off), one pass in 5 minutes otherwise
    every = (chan_poll.interval(db.flag(chan_poll.FLAG)) if chan_poll.active(now) else 0) or 300
    age = own_thread.beat_age(folder, "alerts", at)
    if age is not None and age > max(STALE.total_seconds(), 3 * every):
        out["alerts"] = age
    return out


def check(db: OpsStore, send: Callable[[str], None], now: datetime, in_hours=None) -> str | None:
    """Text the owner when the listener's heartbeat is stale in alert hours. Returns the text sent, else None.
    Never raises (it rides on the control job, which must go on)."""
    try:
        ready(db)
        if in_hours is None:
            from sgt.fast import chan_poll

            in_hours = chan_poll.active
        if not in_hours(now):
            return None
        quiet = silent(db, now)
        if not quiet:
            return None
        if now - _last_warned(db) < AGAIN:
            return None
        with db.read() as c:
            shorts = c.execute("SELECT COUNT(*) FROM fade_trades WHERE status IN ('ask','pending','open')").fetchone()[0]
            longs = c.execute("SELECT COUNT(*) FROM long_trades WHERE status='open'").fetchone()[0]
        mins = lambda s: int(s // 60)  # noqa: E731
        if "short book" in quiet:                           # the pass that also drives everything the owner sees
            seen = now - timedelta(seconds=quiet["short book"])
            held = (f"Open on paper: {shorts} short, {longs} long. Their stops are NOT being checked."
                    if shorts or longs else "Nothing is open.")
            from sgt.ops import own_thread

            # Say only what the signs show (review 2026-11-23): the channel's own sign decides, not the book's.
            seen_read = own_thread.beat_age(Path(db.path).parent, "alerts", now.timestamp())
            read = ("Alerts are not being read. " if "alerts" in quiet or seen_read is None
                    else "The alert channel is still being read. ")
            text = (f"[SGT] ⚠️ The listener has not run for {mins(quiet['short book'])} min "
                    f"(last pass {seen.astimezone(ET):%H:%M} ET). {read}{held} "
                    "It restarts by itself after a crash; if this text repeats, it is stuck.")
        else:
            parts = []
            if "alerts" in quiet:
                parts.append(f"the alert channel has not been checked for {mins(quiet['alerts'])} min (a lost "
                             "connection, or Telegram asking us to slow down): new alerts may NOT be reaching the books")
            if "long book" in quiet:
                parts.append(f"the long book has not checked its positions for {mins(quiet['long book'])} min"
                             + (f" ({longs} open on paper)" if longs else ""))
            text = ("[SGT] ⚠️ Listener problem: " + "; ".join(parts) + ". The short book's stop checks are running. "
                    "If this text repeats, it is stuck.")
        went: list[int] = []

        def tracked(t: str) -> None:
            send(t)
            went.append(1)

        try:
            how = text_guard.send_capped(db, tracked, EVENT, CAP, text, "listener watchdog", now)
        except Exception:  # noqa: BLE001 - the database refused to count or record it (a full disk)
            log.exception("listener watchdog: the text could not be counted or recorded")
            how = "unrecorded"
            if not went:                                    # it failed BEFORE sending: send it anyway. The day's cap
                try:                                        # cannot be read; the marker below still spaces warnings
                    send(text)
                except Exception:  # noqa: BLE001
                    log.exception("listener watchdog: the text could not be sent")
                    how = "failed"
        if how == "failed":
            return None                                     # not sent: no mark, so the next minute tries again
        _mark_warned(db, now)                               # sent, unrecorded, or over the day's limit: quiet for AGAIN
        return text if how in ("sent", "unrecorded") else None
    except Exception:  # noqa: BLE001 - a watchdog that breaks the control job would be worse than none
        log.exception("listener watchdog failed")
        return None
