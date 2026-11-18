"""Trade texts that are never lost silently (owner 2026-11-18: "all of the trades, not just the ones that actually
make money, but the ones that are losing money too").

Every opened trade has always been texted twice, entry and exit, whatever it made (checked against the audit log for
every session to 2026-11-18). Two ways a text could still vanish without a trace, both closed here:
- the per-day limit of its book was reached: the text was skipped and nothing said so;
- the send failed: one log line, no second try, no record.
Now a text over the limit is recorded (``<event>_dropped``) and the FIRST one of the day sends a one-line notice; a
failed send is tried once more and then recorded (``<event>_failed``). The nightly digest lists both. The limits
stay: they are the brake on a runaway loop, set far above any real day.
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Callable

from sgt.fast.fade import ET
from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
TRIES = 2


def day_start(now: datetime) -> datetime:
    return datetime.combine(now.astimezone(ET).date(), datetime.min.time(), tzinfo=ET)


def send_capped(db: OpsStore, send: Callable[[str], None], event: str, cap: int, text: str, label: str,
                now: datetime | None = None) -> str:
    """Send one text under ``event``'s daily limit. Returns "sent", "dropped" (over the limit) or "failed"."""
    since = day_start(now or utcnow())
    if db.count_events(event, since) >= cap:
        first = db.count_events(f"{event}_dropped", since) == 0
        db.audit(f"{event}_dropped", n=len(text))
        log.error("%s text over the daily limit of %d: not sent", label, cap)
        if first:
            try:
                send(f"[SGT] Text limit reached for {label} today ({cap}). Further {label} texts are not sent today; "
                     "every trade is still on the site and in tonight's summary.")
            except Exception as exc:  # noqa: BLE001 - the notice is best effort; the drop is already recorded
                log.error("%s limit notice failed: %s", label, type(exc).__name__)
        return "dropped"
    why = "unknown"
    for _ in range(TRIES):
        try:
            send(text)
        except Exception as exc:  # noqa: BLE001 - tried again once, then recorded; a text never stops a book
            why = type(exc).__name__
            continue
        db.audit(event, n=len(text))
        return "sent"
    db.audit(f"{event}_failed", n=len(text), why=why)
    log.error("%s text failed twice: %s", label, why)
    return "failed"


def missed_line(db: OpsStore, day: date) -> str | None:
    """Digest line: texts that were not delivered that ET day (None when every text went out)."""
    start = datetime.combine(day, datetime.min.time(), tzinfo=ET)
    end = start.replace(hour=23, minute=59, second=59)
    with db.tx() as c:
        rows = c.execute("SELECT event, COUNT(*) FROM audit WHERE at>=? AND at<=? AND (event LIKE '%\\_dropped' ESCAPE '\\' "
                         "OR event LIKE '%\\_failed' ESCAPE '\\') GROUP BY event ORDER BY event",
                         (iso(start), iso(end))).fetchall()
    if not rows:
        return None
    bits = [f"{n} {e.rsplit('_', 1)[0].replace('_', ' ')} ({'over the daily limit' if e.endswith('_dropped') else 'send failed'})"
            for e, n in rows]
    return "texts NOT delivered today: " + ", ".join(bits) + "; the trades themselves are on the site"
