"""Health of the decision record (audit 2026-11-22, phase 3b): an explicit signal, every night, that does not rely on
the writer's own error path.

Three independent checks of one session:
1. recovery: lines the trading path could not write were spooled (``OpsStore.decide``); they are put back first;
2. coverage: every alert row of the two alert books created since the record began has at least one line;
3. completeness against the channel: every channel alert of a tested kind has a line (a new row or a "duplicate").
   An alert posted while the listener was restarting leaves no row and no line (VRAX, 2026-11-04): this is the check
   that shows it, the same night.

The nightly summary prints one line in every case, so a missing line is itself a signal. Nothing here is on a trading
path. Runbook: docs/findings/closing_questions_2026-11-22.md, section 6.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from sgt.fast import fade
from sgt.intraday.calendar import default_calendar
from sgt.ops.store import OpsStore

BOOKS = (("short", "fade_trades"), ("long", "long_trades"))
MATCH_S = 2.0                       # a channel alert and its line carry the same posting time, to the second
MAX_NAMES = 5


def _utc(s: str | None) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace(" ", "T"))
    except (TypeError, ValueError):
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def channel_alerts(path: Path, session: str) -> list[tuple[str, datetime]] | None:
    """(symbol, posted) of the session's channel alerts of a tested kind; None when the channel record cannot be read."""
    if not path.is_file():
        return None
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            rows = c.execute("SELECT symbol, alert_type, posted_at, has_news FROM crowd_alerts WHERE posted_at LIKE ? "
                             "LIMIT 5000", (session + "%",)).fetchall()
        finally:
            c.close()
    except sqlite3.Error:
        logging.getLogger(__name__).exception("decision health: the channel record could not be read")
        return None
    out, cal = [], default_calendar()
    for sym, kind, posted, news in rows:
        t = _utc(posted)
        if t is None or kind not in fade.CELLS or (kind == "news" and news != 1):
            continue                                        # the book's own first test (fade.on_alert)
        if cal.is_session(t.astimezone(fade.ET).date()):    # an alert on a closed day gets no row by design
            out.append((str(sym), t))
    return out


def health(db: OpsStore, session: str, alerts_db: Path | None = None) -> dict:
    """The record's state for one session (ISO date, UTC day of the alerts = the ET session for market hours)."""
    out: dict = {"session": session, "replayed": db.decide_replay(), "waiting": db.spooled(),
                 "lost_here": db.decide_lost}
    try:
        with db.tx() as c:
            start = c.execute("SELECT MIN(at) FROM decisions").fetchone()[0] or ""
            out["lines"] = c.execute("SELECT COUNT(*) FROM decisions WHERE at LIKE ?", (session + "%",)).fetchone()[0]
            rows = covered = 0
            for book, table in BOOKS:
                ids = [r[0] for r in c.execute(f"SELECT id FROM {table} WHERE session=? AND created_at>=?",  # noqa: S608
                                               (session, start))]
                have = {r[0] for r in c.execute("SELECT DISTINCT ref FROM decisions WHERE book=? AND ref IS NOT NULL",
                                                (book,))}
                rows += len(ids)
                covered += sum(1 for i in ids if i in have)
            seen = [(r["symbol"], json.loads(r["facts"] or "{}").get("posted_at")) for r in c.execute(
                "SELECT symbol, facts FROM decisions WHERE book='short' AND state_from IS NULL AND at>=?",
                (session,))]                 # from the day's start on: a line may be written just after midnight UTC
    except Exception as e:  # noqa: BLE001 - a broken record is the finding, not a crash of the summary
        return {**out, "ok": False, "error": repr(e)[:120]}
    out |= {"rows": rows, "covered": covered, "since": start}
    path = alerts_db or Path(db.path).resolve().parent.parent.parent / "sgt.db"
    alerts = channel_alerts(path, session)
    if alerts is None:
        out["channel"] = None
    else:
        lines = [(s, _utc(p)) for s, p in seen]
        after = _utc(start)
        missing = [(s, t) for s, t in alerts if (after is None or t >= after) and not any(
            s == ls and lt is not None and abs((lt - t).total_seconds()) <= MATCH_S for ls, lt in lines)]
        out |= {"channel": len(alerts), "unrecorded": [f"{s} {t:%H:%M:%S}" for s, t in missing]}
    out["ok"] = covered == rows and out["waiting"] == 0 and not out.get("unrecorded") and out["lost_here"] == 0
    return out


def line(db: OpsStore, session: str, alerts_db: Path | None = None) -> str:
    """One line for the nightly summary: always present, a warning when anything is missing."""
    h = health(db, session, alerts_db)
    if "error" in h:
        return f"⚠️ decision record: cannot be read ({h['error']}); {h['waiting']} lines waiting in the spool"
    back = f"; {h['replayed']} late lines put back" if h["replayed"] else ""
    chan = "" if h.get("channel") is None else f"; {h['channel']} channel alerts of a tested kind"
    if h["ok"]:
        return f"decision record: complete ({h['covered']} of {h['rows']} alert rows, {h['lines']} lines{chan}{back})"
    bad = []
    if h["covered"] != h["rows"]:
        bad.append(f"{h['rows'] - h['covered']} of {h['rows']} alert rows have no line")
    if h["waiting"]:
        bad.append(f"{h['waiting']} lines waiting in the spool" if h["waiting"] > 0 else "the spool cannot be read")
    if h.get("unrecorded"):
        names = ", ".join(h["unrecorded"][:MAX_NAMES]) + ("…" if len(h["unrecorded"]) > MAX_NAMES else "")
        bad.append(f"{len(h['unrecorded'])} channel alerts never reached the book (UTC: {names})")
    if h["lost_here"]:
        bad.append(f"{h['lost_here']} lines lost")
    return "⚠️ decision record INCOMPLETE: " + "; ".join(bad) + back
