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

from sgt.fast import fade, would_have
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
_PRICE = re.compile(r"^price \S+ outside \$1-50$")
LONG = {"none": "no long setup filled", "missed": "long setup filled before the book was on", "open": "long open",
        "void": "long void (no trade was possible on the full tape)",
        "nowin": "long setup skipped: its target could not beat costs"}
SEEN = "long seen, not traded"                      # measure-only (owner 2026-11-17): watched, texted, never a trade


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
        return "void (no trade was possible on the full tape)"
    if status in ("pending", "ask"):
        return "waiting for its entry minute"
    return "not shorted"


def long_words(status: str | None, net) -> str:
    if status is None:
        return ""
    if status in ("settled", "est_closed"):
        return "long taken" + (_pct(net) if status == "settled" else " (closed, official tonight)")
    if status == "seen":
        return SEEN + (f": would have made{_pct(net)}" if net is not None else "")
    return LONG.get(status, str(status)[:MAX_TEXT])


def would_words(status: str, net, note) -> str:
    """What an alert the short book did not take would have made (sgt.fast.would_have); blank for a taken one."""
    if status not in would_have.NOT_TAKEN:
        return ""
    if net is not None:
        return f"{net:+.1%}"
    if note == would_have.NO_TRADE:
        return "no result: no regular-hours trade within 5 minutes"
    return str(note)[:MAX_TEXT] if note else "scored tonight"


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
            "SELECT f.id, f.symbol, f.session, f.alert_type, f.posted_at, f.status, f.reason, f.net, f.wh_net, f.wh_note, "
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
                    "would": would_words(r["status"], r["wh_net"], r["wh_note"]),
                    "would_n": r["wh_net"] if r["status"] in would_have.NOT_TAKEN else None,
                    "long": long_words(r["l_status"], r["l_net"]), "traded": bool(shorted or longed)})
    return out


def gates(db: OpsStore, max_sessions: int = MAX_SESSIONS) -> list[dict]:
    """Per session and gate (the reason in plain words): alerts stopped, how many have a would-have result, its
    average, how many would have won, and the sum in dollars at the size of one short today. Most alerts first.
    A measurement for the owner's eye (2026-11-18, option A): never part of a total."""
    with db.tx() as c:
        days = [r[0] for r in c.execute("SELECT DISTINCT session FROM fade_trades ORDER BY session DESC LIMIT ?",
                                        (max_sessions,))]
        if not days:
            return []
        got = c.execute(
            "SELECT session, reason, wh_net FROM fade_trades WHERE status IN (?,?) AND "
            f"session IN ({','.join('?' * len(days))}) ORDER BY id LIMIT ?", (*would_have.NOT_TAKEN, *days, MAX_ROWS * 4)).fetchall()
    try:
        size = float(fade.entry_notional(db))
    except Exception:  # noqa: BLE001 - the dollars are an extra; the list is shown without them
        size = None
    agg: dict[tuple[str, str], list] = {}
    for r in got:
        gate = "price outside $1 to $50" if _PRICE.match((r["reason"] or "").strip()) else (why(r["reason"]) or "no reason stored")
        agg.setdefault((str(r["session"])[:10], gate), []).append(r["wh_net"])
    out = []
    for (session, gate), nets in agg.items():
        x = [float(v) for v in nets if v is not None]
        out.append({"session": session, "gate": gate, "n": len(nets), "scored": len(x),
                    "avg": sum(x) / len(x) if x else None, "won": sum(v > 0 for v in x),
                    "usd": sum(x) * size if x and size is not None else None, "size": size})
    return sorted(out, key=lambda g: (g["session"], g["n"]), reverse=True)
