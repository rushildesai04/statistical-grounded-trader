"""Trade approvals over SMS (2026-11-02; user: approval scope = everything).

Every entry or increase (broker order or approved-book position) is sent as a request with a fresh
code; only ``OK <code>`` from the owner's number before expiry approves it, ``NO <code>``
rejects it, silence lets it expire (fail closed). Trade requests use 2-digit codes (owner request 2026-11-04:
fast replies); a code is never reissued within 24 h, so a late reply can never approve a different trade.
Control actions (RESUME / FLATTEN / ARM) keep 6-digit codes. A reply to an expired request is answered
"too late" and does not count as a wrong code. Codes are single-use; a newer request for the
same strategy supersedes the pending one. Five wrong codes within an hour pause trading and expire
everything pending (a spoofed sender cannot see the codes, which only go to the owner's phone).
Control: PAUSE / DISARM (no code, fail-closed direction), RESUME / FLATTEN / ARM (challenge code first),
STATUS, PENDING, CMDS. ARM enables the fast alert-fade book for the current ET trading day only (2026-11-03). Twilio reserves YES/STOP/HELP/START, so they are not used.
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
CODE_DIGITS = 6                                 # control actions
TRADE_DIGITS = 2                                # trade approvals: "OK 42"
REUSE_WINDOW = timedelta(hours=24)              # no code is reissued within this window
MAX_BAD_CODES = 5
BAD_WINDOW = timedelta(hours=1)
CONTROL_TTL = timedelta(minutes=10)
STALE_EXECUTING = timedelta(minutes=30)
_CODED = re.compile(rf"^(?:(OK|NO)\s+(\d{{{TRADE_DIGITS}}}|\d{{{CODE_DIGITS}}})|(RESUME|FLATTEN|ARM)\s+(\d{{{CODE_DIGITS}}}))$")
_WORD = re.compile(r"^(STATUS|PENDING|PAUSE|RESUME|FLATTEN|ARM|DISARM|CMDS|\?)$")

HELP = ("SGT commands: STATUS, PENDING, PAUSE, RESUME (sends a code), FLATTEN (sends a code), "
        "ARM (sends a code; fast trades for today) / DISARM, OK 42 / NO 42 to answer a trade request.")


@dataclass(frozen=True, slots=True)
class Command:
    verb: str
    code: str | None = None


def parse(body: str) -> Command | None:
    text = " ".join((body or "").strip().upper().split())[:40]
    if m := _CODED.match(text):
        return Command(m.group(1), m.group(2)) if m.group(1) else Command(m.group(3), m.group(4))
    if m := _WORD.match(text):
        return Command("CMDS" if m.group(1) == "?" else m.group(1))
    return None


def _new_code(c: sqlite3.Connection, digits: int, now: datetime) -> str:
    """Unused code: not pending and not issued within REUSE_WINDOW. 2-digit codes are 10-99 (no leading zero);
    if all 90 are taken the request falls back to 6 digits rather than ever reusing one."""
    used = {r[0] for r in c.execute("SELECT code FROM approvals WHERE status='pending' OR created_at>=?",
                                    (iso(now - REUSE_WINDOW),))}
    if digits == TRADE_DIGITS:
        free = [str(x) for x in range(10, 100) if str(x) not in used]
        if free:
            return free[secrets.randbelow(len(free))]
        digits = CODE_DIGITS
    while (code := f"{secrets.randbelow(10 ** digits):0{digits}d}") in used:
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
        code = _new_code(c, CODE_DIGITS if kind == "control" else TRADE_DIGITS, now)
        aid = c.execute("INSERT INTO approvals(code, strategy, kind, period, summary, payload, created_at, expires_at,"
                        " status) VALUES (?,?,?,?,?,?,?,?,'pending')",
                        (code, strategy, kind, period, summary, json.dumps(payload, default=str),
                         iso(now), iso(expires_at))).lastrowid
    verb = payload.get("action", "").upper() if kind == "control" else "OK"
    tail = (f"\nReply {verb} {code} to confirm (expires {fmt_et(expires_at)})." if kind == "control" else
            f"\nEnters automatically at {expires_at.astimezone(ET):%H:%M:%S} ET unless you reply NO {code}. "
            f"OK {code} enters now." if kind == "fade" else
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
    want_kind = "control" if cmd.verb in ("RESUME", "FLATTEN", "ARM") else None
    with db.tx() as c:
        row = c.execute("SELECT * FROM approvals WHERE status='pending' AND code=?", (cmd.code,)).fetchone()
        ok = row is not None and ((row["kind"] == "control") == (want_kind == "control"))
        if ok and want_kind == "control":
            ok = json.loads(row["payload"]).get("action", "").upper() == cmd.verb
        paused = c.execute("SELECT value FROM flags WHERE key='paused'").fetchone()
        if ok and row["kind"] != "control" and cmd.verb == "OK" and paused is not None and paused[0] == "1":
            c.execute("UPDATE approvals SET status='expired', decided_at=?, decided_sid=? WHERE id=?",
                      (iso(now), sid, row["id"]))
            return "Trading is PAUSED: request not approved (expired). RESUME first, then wait for a new request.", None
        if ok:
            status = "rejected" if cmd.verb == "NO" else "approved"
            c.execute("UPDATE approvals SET status=?, decided_at=?, decided_sid=? WHERE id=? AND status='pending'",
                      (status, iso(now), sid, row["id"]))
            row = c.execute("SELECT * FROM approvals WHERE id=?", (row["id"],)).fetchone()
    if not ok:
        with db.tx() as c:                          # a real code that simply expired: "too late", not an attack
            late = c.execute("SELECT * FROM approvals WHERE code=? AND status!='pending' AND created_at>=? "
                             "ORDER BY id DESC LIMIT 1", (cmd.code, iso(now - REUSE_WINDOW))).fetchone()
        if late is not None and late["status"] in ("executed", "executing", "approved", "rejected"):
            db.audit("late_code", id=late["id"], sid=sid)
            done = "already entered; it exits automatically" if late["status"] != "rejected" else "already skipped"
            return f"No change: {late['summary'][:120]} {done}.", None
        if late is not None and late["status"] in ("expired", "superseded", "failed"):
            db.audit("late_code", id=late["id"], sid=sid)
            hint = " Text ARM in the morning to auto-enter fast trades." if late["kind"] == "fade" else ""
            why = {"expired": f"expired at {fmt_et(datetime.fromisoformat(late['expires_at']))}",
                   "superseded": "was replaced by a newer request", "failed": "could not be completed"}[late["status"]]
            return f"Too late, not approved: {late['summary'][:120]} {why}." + hint, None
        db.audit("bad_code", verb=cmd.verb, sid=sid)
        if db.count_events("bad_code", now - BAD_WINDOW) >= MAX_BAD_CODES:
            lockout(db, now)
            return "Too many wrong codes: trading PAUSED and all requests expired. Text RESUME to unlock.", None
        return f"No pending request with code {cmd.code} (it may have expired). Text PENDING to list.", None
    db.audit("decided", id=row["id"], status=row["status"], sid=sid)
    return f"{row['status'].upper()}: {row['summary'][:200]}", row


def pause(db: OpsStore, now: datetime | None = None, reason: str = "pause") -> int:
    """Stop new exposure: set the flag, expire pending trade requests, void approved book entries not yet
    filled (automatic exits still apply). Returns the number of voided entries."""
    now = now or utcnow()
    db.set_flag("paused", "1")
    db.set_flag("fast_armed", "")             # fast trades need a fresh ARM after any pause / lockout
    with db.tx() as c:
        c.execute("UPDATE approvals SET status='expired', decided_at=?, result='paused' WHERE status='pending' "
                  "AND kind!='control'",
                  (iso(now),))
        voided = c.execute("UPDATE book_orders SET status='void' WHERE status='scheduled' AND source='approved'"
                           ).rowcount
    db.audit(reason, voided=voided)
    return voided


def lockout(db: OpsStore, now: datetime) -> None:
    pause(db, now, reason="lockout")
    with db.tx() as c:
        c.execute("UPDATE approvals SET status='expired', decided_at=?, result='lockout' WHERE status='pending'",
                  (iso(now),))


def challenge(db: OpsStore, send, action: str, now: datetime | None = None) -> int | None:
    """RESUME / FLATTEN / ARM: text a one-time code that must be echoed back."""
    now = now or utcnow()
    expire(db, now)
    with db.tx() as c:
        if c.execute("SELECT 1 FROM approvals WHERE status='pending' AND strategy=?", (f"control:{action}",)).fetchone():
            return None                       # one live challenge per action: a spoofed repeat cannot replace it
    what = {"resume": "Resume trading", "flatten": "FLATTEN: close every broker position and zero the approved book",
            "arm": f"ARM fast alert-fade trades for {now.astimezone(ET):%a %m-%d} (auto-entry, capped)"}
    return request(db, send, strategy=f"control:{action}", kind="control", period=now.date().isoformat(),
                   summary=what[action], payload={"action": action}, expires_at=now + CONTROL_TTL, now=now)


def claim(db: OpsStore, approval_id: int, now: datetime | None = None) -> bool:
    """approved -> executing, at most once and only before the expiry the owner was shown."""
    now = now or utcnow()
    with db.tx() as c:
        return c.execute("UPDATE approvals SET status='executing', decided_at=? WHERE id=? AND status='approved' "
                         "AND expires_at>?", (iso(now), approval_id, iso(now))).rowcount == 1


def get(db: OpsStore, approval_id: int) -> sqlite3.Row | None:
    with db.tx() as c:
        return c.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()


def sweep(db: OpsStore, now: datetime | None = None) -> list[sqlite3.Row]:
    """Fail approvals that can no longer complete: approved but past expiry, or executing too long
    (process killed / machine restarted). Returned rows are texted to the owner."""
    now = now or utcnow()
    with db.tx() as c:
        rows = c.execute("SELECT * FROM approvals WHERE (status='approved' AND expires_at<=?) OR "
                         "(status='executing' AND decided_at<=?)", (iso(now), iso(now - STALE_EXECUTING))).fetchall()
        for r in rows:
            c.execute("UPDATE approvals SET status='failed', result=? WHERE id=? AND status=?",
                      (f"stale {r['status']}: not completed; check the broker", r["id"], r["status"]))
    for r in rows:
        db.audit("swept", id=r["id"], was=r["status"])
    return rows


def attention(db: OpsStore, since: datetime) -> list[sqlite3.Row]:
    """Approved/executing now, or failed since ``since`` (for the digest)."""
    with db.tx() as c:
        return c.execute("SELECT * FROM approvals WHERE status IN ('approved','executing') OR (status='failed' "
                         "AND COALESCE(decided_at, created_at)>=?) ORDER BY id", (iso(since),)).fetchall()


def finish(db: OpsStore, approval_id: int, ok: bool, result: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE approvals SET status=?, result=? WHERE id=?",
                  ("executed" if ok else "failed", result[:2000], approval_id))
    db.audit("executed" if ok else "execute_failed", id=approval_id, result=result[:500])


def arm(db: OpsStore, now: datetime | None = None) -> str:
    """Enable fast trades for the current ET date only; expires at midnight ET by construction."""
    day = (now or utcnow()).astimezone(ET).date().isoformat()
    db.set_flag("fast_armed", day)
    db.audit("arm", day=day)
    return day


def disarm(db: OpsStore, reason: str = "disarm", now: datetime | None = None) -> None:
    """No new fast trades today: clears ARM and also blocks auto-entry at expiry for the rest of the ET day."""
    day = (now or utcnow()).astimezone(ET).date().isoformat()
    db.set_flag("fast_armed", f"off:{day}")
    db.audit(reason)


def disarmed(db: OpsStore, now: datetime | None = None) -> bool:
    """DISARM was sent today (ET) or trading is paused: no fast entries of any kind."""
    day = (now or utcnow()).astimezone(ET).date().isoformat()
    return db.flag("fast_armed") == f"off:{day}" or db.paused()


def armed(db: OpsStore, now: datetime | None = None) -> bool:
    """Fast entries need ARM for today AND not paused (PAUSE always wins)."""
    day = (now or utcnow()).astimezone(ET).date().isoformat()
    return db.flag("fast_armed") == day and not db.paused()
