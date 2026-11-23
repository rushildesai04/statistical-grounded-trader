"""Durable alert identifiers, the channel cursor and recorded gaps (listener recovery, 2026-11-22).

Until this change "already handled" lived in the listener's memory, so an alert posted while the process was down was
never handled and a message handled twice could leave two rows (docs/findings/ops_risks_2026-11-22.md, section 2).

Three small pieces of durable state, all in the ops database:

- ``alert_events``: ONE row per alert of a message, keyed (channel, message id, symbol, alert kind). The short book
  writes it in the same transaction as the alert's own row (``fade._insert``), so "this alert was handled" and "this
  is what the book did with it" cannot disagree, and a second delivery of the same alert finds the key and does
  nothing: no second row, no second decision line, no second text.
- the cursor (flag ``alert_cursor:<channel>``): the highest message id up to which EVERY alert of EVERY message is
  in ``alert_events``. ``advance`` checks that in the transaction that moves it, and never moves it backwards.
- ``alert_gaps``: every stretch of the channel the listener decided NOT to handle (too old to matter, unreadable, or
  failing after its retries), written in the transaction that moves the cursor past it. The cursor never passes an
  unhandled message without such a row; the nightly summary names them.

Nothing here knows about trading rules. ``sgt.fast.alert_recovery`` drives it.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime

from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
Key = tuple[str, int, str, str]                 # channel, message id, symbol, alert kind
CURSOR = "alert_cursor:"
VIAS = ("push", "poll", "replay")
NONE, DUPLICATE, FAILED = "none", "duplicate", "failed"   # alert_events.status of an alert that left no row
_WHERE = "channel=? AND message_id=? AND symbol=? AND alert_type=?"


def key(a) -> Key:
    """The durable identifier of one alert (anything with channel, message_id, symbol, alert_type)."""
    return str(a.channel), int(a.message_id), str(a.symbol), str(a.alert_type)


def find(c: sqlite3.Connection, k: Key):
    """The alert's record, or None when it was never handled."""
    return c.execute(f"SELECT * FROM alert_events WHERE {_WHERE}", k).fetchone()  # noqa: S608 - fixed text


def put(c: sqlite3.Connection, k: Key, posted_at: datetime, received_at: datetime, via: str | None,
        recovered_at: datetime | None, status: str, reason: str | None, fade_id: int | None) -> None:
    """Record that the alert was handled, inside the caller's transaction. Raises ``sqlite3.IntegrityError`` when it
    already is on record: the caller's whole transaction then rolls back, which is the point."""
    c.execute("INSERT INTO alert_events(channel, message_id, symbol, alert_type, posted_at, received_at, via, "
              "recovered_at, status, reason, fade_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
              (*k, iso(posted_at), iso(received_at), via, iso(recovered_at) if recovered_at else None, status,
               (reason or None) and str(reason)[:300], fade_id))


def note(db: OpsStore, k: Key, posted_at: datetime, received_at: datetime, via: str | None,
         recovered_at: datetime | None, status: str, reason: str | None) -> bool:
    """Record an alert that leaves no row of its own (not a tested kind, a closed day, a failure given up on). False
    when it was already on record."""
    try:
        with db.tx() as c:
            put(c, k, posted_at, received_at, via, recovered_at, status, reason, None)
        return True
    except sqlite3.IntegrityError:
        return False


def _cursor(c: sqlite3.Connection, channel: str) -> int | None:
    r = c.execute("SELECT value FROM flags WHERE key=?", (CURSOR + channel,)).fetchone()
    try:
        v = int(r[0]) if r is not None else None
    except (TypeError, ValueError):
        return None
    return v if v is not None and v > 0 else None


def cursor(db: OpsStore, channel: str) -> int | None:
    """The highest message id fully handled; None when it was never set (or cannot be read: treated as never set,
    which the caller reports, since asking the channel for "everything after 0" would be its whole history)."""
    with db.read() as c:
        return _cursor(c, channel)


def _move(c: sqlite3.Connection, channel: str, message_id: int) -> None:
    c.execute("INSERT INTO flags(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET "
              "value=excluded.value, updated_at=excluded.updated_at", (CURSOR + channel, str(int(message_id)),
                                                                       utcnow().isoformat()))


def baseline(db: OpsStore, channel: str, message_id: int) -> bool:
    """Set the cursor for the first time (nothing older is replayed). False when one is already set."""
    with db.tx() as c:
        if _cursor(c, channel) is not None:
            return False
        _move(c, channel, message_id)
    return True


def advance(db: OpsStore, channel: str, message_id: int, keys: list[Key]) -> bool:
    """Move the cursor to ``message_id``: only when every alert of that message is on record (checked in the same
    transaction) and never backwards. False = an alert is missing, the cursor stays."""
    with db.tx() as c:
        for k in keys:
            if find(c, k) is None:
                return False
        cur = _cursor(c, channel)
        if cur is None or cur < message_id:
            _move(c, channel, message_id)
    return True


def skip(db: OpsStore, channel: str, from_id: int | None, to_id: int, reason: str, n: int | None = None,
         failed: list[tuple] | None = None, **detail) -> list[str]:
    """Move the cursor past messages that are NOT handled, and write down that it did, in one transaction. The only
    way the cursor passes an unhandled message. ``failed``: alerts given up on, as (key, posted_at, received_at, via,
    recovered_at); each one not yet on record is recorded as FAILED in the SAME transaction, so a crash can never
    leave a failed alert without its gap row (review 2026-11-22). Returns the symbols recorded as failed."""
    bad: list[str] = []
    with db.tx() as c:
        for k, posted, got, via, rec in failed or ():
            if find(c, k) is None:
                put(c, k, posted, got, via, rec, FAILED, reason, None)
                bad.append(k[2])
        if bad:
            detail = {**detail, "symbols": bad}
        c.execute("INSERT INTO alert_gaps(at, channel, from_id, to_id, n, reason, detail) VALUES (?,?,?,?,?,?,?)",
                  (utcnow().isoformat(), channel, from_id, int(to_id), n, reason[:200],
                   json.dumps(detail, default=str)[:2000] if detail else None))
        cur = _cursor(c, channel)
        if cur is None or cur < to_id:
            _move(c, channel, to_id)
    log.warning("alert channel %s: messages %s..%s NOT handled (%s)", channel, from_id, to_id, reason)
    return bad


def failed(db: OpsStore, since: str, until: str) -> int:
    """Alerts given up on (status FAILED) that were received in ``since`` .. ``until`` (ISO text bounds), however
    they arrived."""
    with db.read() as c:
        return int(c.execute("SELECT COUNT(*) FROM alert_events WHERE status=? AND received_at>=? AND received_at<?",
                             (FAILED, since, until)).fetchone()[0])


def gaps(db: OpsStore, since: str) -> list[dict]:
    """The recorded gaps written at or after ``since`` (an ISO time or date prefix compares as text)."""
    with db.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM alert_gaps WHERE at>=? ORDER BY id LIMIT 200", (since,))]
