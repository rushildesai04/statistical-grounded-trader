"""Every alert of a day and what the agent did with it (owner 2026-11-16: "i see literally nothing from today").

The site listed trades only, so a day on which no alert passed the rules was a blank page, indistinguishable from a
system that is down. This is the plain list behind the "Alerts" section of the Fade tab: one line per alert the
listener stored (alerts in the tested cells; ``fade_trades``), with the short's and the long's outcome in plain words.
Pure over the ops DB; no network. Symbols and alert types come from the channel: untrusted text, bounded here and
written to the page with ``textContent`` only.
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sgt.fast import fade
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")
MAX_SESSIONS, MAX_ROWS, MAX_TEXT = 5, 400, 120
ENTERED = ("open", "est_closed", "settled")
# Stored reason -> the owner's words. Anything not listed is shown as stored, without its bracketed code reference.
WHY = {
    fade.NEWS_REASON: "company news today: fades on news names only break even",
    fade.BETA_REASON: "very jumpy stock (beta 3 or more): no shorts",
    fade.REPEAT_REASON: "alerted in the last 7 days: multi-day runners keep running",
    fade.OFF_REASON: "the fade was switched off at that time",
    fade.BORROW_NONE: "no shares to borrow at IBKR: a real account could not short it",
    fade.BORROW_FEW: "too few shares to borrow at IBKR for this trade size",
    fade.BORROW_UNKNOWN: "borrow unknown at entry: no short",
    fade.NO_PRINT_REASON: "no trade printed within 5 minutes (halted or no quote)",
    "alert received late": "the alert reached us too late to enter at the tested time",
    "declined": "you declined it",
}
_CODE = re.compile(r"\s*\((?:[^()]*(?:amendment|owner|prereg)[^()]*)\)")
_WINDOW = re.compile(r"^outside 09:30-(\d\d:\d\d)$")
LONG = {"none": "no long setup filled", "missed": "long setup filled before the book was on", "open": "long open",
        "void": "long void (did not fill on official bars)"}


def why(reason: str | None) -> str:
    """A stored skip / filter reason in plain words (bounded)."""
    r = (reason or "").strip()
    if r in WHY:
        return WHY[r]
    if m := _WINDOW.match(r):
        return f"outside the rule's hours (09:30 to {m.group(1)} ET)"
    return _CODE.sub("", r)[:MAX_TEXT]


def _pct(x) -> str:
    return "" if x is None else f" {x:+.1%}"


def short_words(status: str, net) -> str:
    if status == "settled":
        return "shorted" + _pct(net)
    if status == "est_closed":
        return "shorted (closed, official tonight)"
    if status == "open":
        return "short open"
    if status == "void":
        return "void (no official fill)"
    if status in ("pending", "ask"):
        return "waiting for its entry minute"
    return "not shorted"


def long_words(status: str | None, net) -> str:
    if status is None:
        return ""
    if status in ("settled", "est_closed"):
        return "long taken" + (_pct(net) if status == "settled" else " (closed, official tonight)")
    return LONG.get(status, str(status)[:MAX_TEXT])


def _et(posted_at: str | None) -> str:
    try:
        return datetime.fromisoformat(str(posted_at)).astimezone(ET).strftime("%H:%M")
    except (TypeError, ValueError):
        return ""


def rows(db: OpsStore, max_sessions: int = MAX_SESSIONS, limit: int = MAX_ROWS) -> list[dict]:
    """Alerts of the latest ``max_sessions`` sessions that have any, newest first: session, time ET, symbol, alert
    type, what the short did and why not, what the long did, and whether either book traded it."""
    with db.tx() as c:
        days = [r[0] for r in c.execute("SELECT DISTINCT session FROM fade_trades ORDER BY session DESC LIMIT ?",
                                        (max_sessions,))]
        if not days:
            return []
        got = c.execute(
            "SELECT f.id, f.symbol, f.session, f.alert_type, f.posted_at, f.status, f.reason, f.net, "
            "l.status AS l_status, l.net AS l_net FROM fade_trades f LEFT JOIN long_trades l ON l.fade_id=f.id "
            f"WHERE f.session IN ({','.join('?' * len(days))}) ORDER BY f.session DESC, f.posted_at DESC, f.id DESC "
            "LIMIT ?", (*days, limit)).fetchall()
    out = []
    for r in got:
        shorted = r["status"] in ENTERED
        longed = r["l_status"] in ENTERED
        out.append({"id": r["id"], "session": str(r["session"])[:10], "at": _et(r["posted_at"]),
                    "symbol": str(r["symbol"])[:12], "type": str(r["alert_type"] or "")[:40],
                    "short": short_words(r["status"], r["net"]),
                    "why": "" if shorted or r["status"] in ("pending", "ask") else why(r["reason"]),
                    "long": long_words(r["l_status"], r["l_net"]), "traded": bool(shorted or longed)})
    return out
