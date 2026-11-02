"""Trade approvals over SMS (2026-11-02; user: approval scope = everything).

Every entry or increase (broker order or approved-book position) is sent as a request with a fresh
6-digit code; only ``OK <code>`` from the owner's number before expiry approves it, ``NO <code>``
rejects it, silence lets it expire (fail closed). Codes are single-use; a newer request for the
same strategy supersedes the pending one. Five wrong codes within an hour pause trading and expire
everything pending (a spoofed sender cannot see the codes, which only go to the owner's phone).
Control: PAUSE (no code, fail-closed direction), RESUME / FLATTEN (challenge code first),
STATUS, PENDING, CMDS. Twilio reserves YES/STOP/HELP/START, so they are not used.
"""
from __future__ import annotations

import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sgt.ops.store import OpsStore, iso, utcnow

ET = ZoneInfo("America/New_York")
CODE_DIGITS = 6
MAX_BAD_CODES = 5
BAD_WINDOW = timedelta(hours=1)
CONTROL_TTL = timedelta(minutes=10)
_CODED = re.compile(rf"^(OK|NO|RESUME|FLATTEN)\s+(\d{{{CODE_DIGITS}}})$")
_WORD = re.compile(r"^(STATUS|PENDING|PAUSE|RESUME|FLATTEN|CMDS|\?)$")

HELP = ("SGT commands: STATUS, PENDING, PAUSE, RESUME (sends a code), FLATTEN (sends a code), "
        "OK <code> / NO <code> to answer a trade request.")


@dataclass(frozen=True, slots=True)
class Command:
    verb: str
    code: str | None = None


def parse(body: str) -> Command | None:
    text = " ".join((body or "").strip().upper().split())[:40]
    if m := _CODED.match(text):
        return Command(m.group(1), m.group(2))
    if m := _WORD.match(text):
        return Command("CMDS" if m.group(1) == "?" else m.group(1))
    return None


def _new_code(c: sqlite3.Connection) -> str:
    live = {r[0] for r in c.execute("SELECT code FROM approvals WHERE status='pending'")}
    while (code := f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}") in live:
        pass
    return code


def fmt_et(ts: datetime) -> str:
    return ts.astimezone(ET).strftime("%a %m-%d %H:%M ET")


def request(db: OpsStore, send, *, strategy: str, kind: str, period: str, summary: str, payload: dict,
            expires_at: datetime, now: datetime | None = None) -> int | None:
    """Create a pending approval and text it. Returns its id, or None when trading is paused.

    ``send(text)`` raising leaves the request FAILED (nobody holds its code, so it can never be approved)."""
    now = now or utcnow()
    if kind != "control" and db.paused():
        db.audit("request_skipped_paused", strategy=strategy, period=period)
        return None
    if expires_at <= now:
        raise ValueError("expires_at must be in the future")
    with db.tx() as c:
        c.execute("UPDATE approvals SET status='superseded', decided_at=? WHERE status='pending' AND strategy=? "
                  "AND kind=?", (iso(now), strategy, kind))
        code = _new_code(c)
        aid = c.execute("INSERT INTO approvals(code, strategy, kind, period, summary, payload, created_at, expires_at,"
                        " status) VALUES (?,?,?,?,?,?,?,?,'pending')",
                        (code, strategy, kind, period, summary, json.dumps(payload, default=str),
                         iso(now), iso(expires_at))).lastrowid
    verb = payload.get("action", "").upper() if kind == "control" else "OK"
    tail = (f"\nReply {verb} {code} to confirm (expires {fmt_et(expires_at)})." if kind == "control" else
            f"\nReply OK {code} to approve, NO {code} to skip. Expires {fmt_et(expires_at)}.")
    try:
        send(f"[SGT] {summary}{tail}")
    except Exception as exc:  # noqa: BLE001 - recorded; the request can never be approved
        with db.tx() as c:
            c.execute("UPDATE approvals SET status='failed', result=? WHERE id=?", (f"sms: {type(exc).__name__}", aid))
        db.audit("request_send_failed", id=aid, strategy=strategy)
        return None
    db.audit("request", id=aid, strategy=strategy, kind=kind, period=period)
    return aid


def expire(db: OpsStore, now: datetime | None = None) -> int:
    now = now or utcnow()
    with db.tx() as c:
        return c.execute("UPDATE approvals SET status='expired', decided_at=? WHERE status='pending' AND expires_at<=?",
                         (iso(now), iso(now))).rowcount


def pending(db: OpsStore) -> list[sqlite3.Row]:
    with db.tx() as c:
        return c.execute("SELECT * FROM approvals WHERE status='pending' ORDER BY id").fetchall()


def latest(db: OpsStore, strategy: str, period: str) -> sqlite3.Row | None:
    with db.tx() as c:
        return c.execute("SELECT * FROM approvals WHERE strategy=? AND period=? ORDER BY id DESC LIMIT 1",
                         (strategy, period)).fetchone()


def answer(db: OpsStore, cmd: Command, sid: str, now: datetime | None = None) -> tuple[str, sqlite3.Row | None]:
    """Apply an OK/NO/RESUME/FLATTEN code. Returns (reply text, the decided row or None)."""
    now = now or utcnow()
    expire(db, now)
    want_kind = "control" if cmd.verb in ("RESUME", "FLATTEN") else None
    with db.tx() as c:
        row = c.execute("SELECT * FROM approvals WHERE status='pending' AND code=?", (cmd.code,)).fetchone()
        ok = row is not None and ((row["kind"] == "control") == (want_kind == "control"))
        if ok and want_kind == "control":
            ok = json.loads(row["payload"]).get("action", "").upper() == cmd.verb
        if ok:
            status = "rejected" if cmd.verb == "NO" else "approved"
            c.execute("UPDATE approvals SET status=?, decided_at=?, decided_sid=? WHERE id=? AND status='pending'",
                      (status, iso(now), sid, row["id"]))
            row = c.execute("SELECT * FROM approvals WHERE id=?", (row["id"],)).fetchone()
    if not ok:
        db.audit("bad_code", verb=cmd.verb, sid=sid)
        if db.count_events("bad_code", now - BAD_WINDOW) >= MAX_BAD_CODES:
            lockout(db, now)
            return "Too many wrong codes: trading PAUSED and all requests expired. Text RESUME to unlock.", None
        return f"No pending request with code {cmd.code} (it may have expired). Text PENDING to list.", None
    db.audit("decided", id=row["id"], status=row["status"], sid=sid)
    return f"{row['status'].upper()}: {row['summary'][:200]}", row


def lockout(db: OpsStore, now: datetime) -> None:
    db.set_flag("paused", "1")
    with db.tx() as c:
        c.execute("UPDATE approvals SET status='expired', decided_at=? WHERE status='pending'", (iso(now),))
    db.audit("lockout")


def challenge(db: OpsStore, send, action: str, now: datetime | None = None) -> int | None:
    """RESUME / FLATTEN: text a one-time code that must be echoed back."""
    now = now or utcnow()
    what = {"resume": "Resume trading", "flatten": "FLATTEN: close every broker position and zero the approved book"}
    return request(db, send, strategy=f"control:{action}", kind="control", period=now.date().isoformat(),
                   summary=what[action], payload={"action": action}, expires_at=now + CONTROL_TTL, now=now)


def claim(db: OpsStore, approval_id: int) -> bool:
    """approved -> executing, at most once (guards against double execution)."""
    with db.tx() as c:
        return c.execute("UPDATE approvals SET status='executing' WHERE id=? AND status='approved'",
                         (approval_id,)).rowcount == 1


def finish(db: OpsStore, approval_id: int, ok: bool, result: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE approvals SET status=?, result=? WHERE id=?",
                  ("executed" if ok else "failed", result[:2000], approval_id))
    db.audit("executed" if ok else "execute_failed", id=approval_id, result=result[:500])
