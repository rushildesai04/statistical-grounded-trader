"""Every alert of a day and what the agent did with it (owner 2026-11-16: "i see literally nothing from today").

The site listed trades only, so a day on which no alert passed the rules was a blank page, indistinguishable from a
system that is down. This is the plain list behind the "Alerts" section of the Fade tab: one line per alert the
listener stored (alerts in the tested cells; ``fade_trades``), with the short's and the long's outcome in plain words.
Pure over the ops DB; no network. Symbols and alert types come from the channel: untrusted text, bounded here and
written to the page with ``textContent`` only.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sgt.fast import alert_context, fade, would_have
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")
MAX_SESSIONS, MAX_ROWS, MAX_TEXT = 5, 400, 120
ENTERED = ("open", "est_closed", "settled")
# Stored reason -> the owner's words. Anything not listed is shown as stored, without its bracketed code reference.
WHY = {
    fade.NEWS_REASON: "company news today: fades on news names only break even",
    fade.BETA_REASON: "very jumpy stock (beta 3 or more): no shorts",
    fade.BETA_UNKNOWN: "beta unknown at entry (lookup failed or a new listing): no short",
    fade.REPEAT_REASON: "alerted in the last 7 days: multi-day runners keep running",
    fade.OFF_REASON: "the fade was switched off at that time",
    # Owner 2026-11-24: one broker's list at one time, not proof that no broker had shares. (The stored reason
    # keeps its old text: it is the key the gate counts and the would-have record match on.)
    fade.BORROW_NONE: "no shares listed to borrow at IBKR at the alert (that broker, that time): no short",
    fade.BORROW_FEW: "too few shares listed at IBKR at the alert for this trade size: no short",
    fade.BORROW_UNKNOWN: "borrow unknown at entry: no short",
    fade.NO_PRINT_REASON: "no trade printed within 5 minutes (halted or no quote)",
    fade.SUBSTITUTE_REASON: "its entry minute passed before our free feed showed a price: recorded, no replacement short",
    "alert received late": "the alert reached us too late to enter at the tested time",
    fade.RESTART_NO_PRINT: "the stock traded, but not after our listener was back, inside the 5 minutes",
    fade.RECOVERED_ONLY: "posted while our listener was down: recorded, not shorted",
    "declined": "you declined it",
}
_CODE = re.compile(r"\s*\((?:[^()]*(?:amendment|owner|prereg)[^()]*)\)")
_WINDOW = re.compile(r"^outside 09:30-(\d\d:\d\d)$")
_PRICE = re.compile(r"^price \S+ outside \$1-50$")
LONG = {"none": "no long setup filled", "missed": "long setup filled before the book was on", "open": "long open",
        "void": "long void (no trade was possible on the full tape)",
        "nowin": "long setup skipped: its target could not beat costs"}
COSTLY = "long not bought: trading cost above 1%"   # owner 2026-11-20: listed with what it would have made
SHORT_OPEN = "long not bought: our short on it was open"   # owner 2026-11-20 (one account, one side)
OVER = "long not opened: already over when the book saw it"   # owner 2026-11-25: record-only, replayed nightly
# owner 2026-11-19 ("yes"): a channel alert that never became a row is listed too, so nothing is unknown. These come
# from the channel's own history (the caller reads it); they are never written to ``fade_trades``, so no rule, count
# or repeat check can see them.
NOT_IN_RULES = "alert type not in the rules"
NO_HEADLINE = "news tag with no headline attached: not in the rules"
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


def recovered_words(via, posted_at, recovered_at) -> str:
    """The replay label of an alert that was posted while the listener was down (sgt.fast.alert_recovery); else ''."""
    if via != "replay":
        return ""
    a, b = _when(posted_at), _when(recovered_at)
    late = f", {int((b - a).total_seconds())} s after the post" if a and b and b >= a else ""
    return f" [recovered after a restart{late}]"


def long_words(status: str | None, net) -> str:
    if status is None:
        return ""
    if status in ("settled", "est_closed"):
        return "long taken" + (_pct(net) if status == "settled" else " (closed, official tonight)")
    if status == "seen":
        return SEEN + (f": would have made{_pct(net)}" if net is not None else "")
    if status == "costly":
        return COSTLY + (f": would have made{_pct(net)}" if net is not None else "")
    if status == "short_open":
        return SHORT_OPEN + (f": would have made{_pct(net)}" if net is not None else "")
    if status == "over":
        return OVER + (f": its order replayed on official candles{_pct(net)}" if net is not None else "")
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


def _when(posted_at) -> datetime | None:
    """A stored posting time as an aware datetime (a time with no zone is UTC); None when it cannot be read."""
    try:
        t = posted_at if isinstance(posted_at, datetime) else datetime.fromisoformat(str(posted_at).strip().replace(" ", "T"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo is not None else t.replace(tzinfo=timezone.utc)


def unlisted(db: OpsStore, channel_alerts: list[dict] | None, max_sessions: int = MAX_SESSIONS,
             limit: int = MAX_ROWS) -> list[dict]:
    """Channel alerts (symbol, alert_type, posted_at, has_news, message_id) of the listed sessions that never became
    a row: an alert type the rules do not use, or a news tag with no headline. One list row each, newest first.
    Untrusted channel text: bounded here like every other row."""
    if not channel_alerts:
        return []
    with db.tx() as c:
        days = [str(r[0])[:10] for r in c.execute("SELECT DISTINCT session FROM fade_trades ORDER BY session DESC LIMIT ?",
                                                  (max_sessions,))]
        if not days:
            return []
        known = {(r[0], str(r[1])) for r in c.execute(
            f"SELECT message_id, symbol FROM fade_trades WHERE session IN ({','.join('?' * len(days))})", days)}  # noqa: S608
    out = []
    for a in channel_alerts:
        t, sym = _when(a.get("posted_at")), str(a.get("symbol") or "")
        if t is None or not sym or (a.get("message_id"), sym) in known:
            continue
        et = t.astimezone(ET)
        if et.date().isoformat() not in days:
            continue
        typ = str(a.get("alert_type") or "")
        out.append({"id": None, "session": et.date().isoformat(), "at": et.strftime("%H:%M"), "symbol": sym[:12],
                    "type": typ[:40], "short": "not shorted", "why": NO_HEADLINE if typ == "news" else NOT_IN_RULES,
                    "would": "", "would_n": None, "long": "", "traded": False, "context": "", "headline": "", "_t": t})
    out.sort(key=lambda r: r["_t"], reverse=True)
    return out[:limit]


def rows(db: OpsStore, max_sessions: int = MAX_SESSIONS, limit: int = MAX_ROWS,
         channel_alerts: list[dict] | None = None) -> list[dict]:
    """Alerts of the latest ``max_sessions`` sessions that have any, newest first: session, time ET, symbol, alert
    type, what the short did and why not, what the long did, and whether either book traded it. With
    ``channel_alerts`` (the channel's own history) the alerts that never became a row are listed among them."""
    with db.tx() as c:
        days = [r[0] for r in c.execute("SELECT DISTINCT session FROM fade_trades ORDER BY session DESC LIMIT ?",
                                        (max_sessions,))]
        if not days:
            return []
        got = c.execute(
            "SELECT f.id, f.symbol, f.session, f.alert_type, f.posted_at, f.status, f.reason, f.net, f.wh_net, f.wh_note, "
            "f.via, f.recovered_at, "
            f"f.ctx_head, f.news_head, {', '.join('f.' + k.strip() for k in alert_context.CTX_COLS.split(','))}, "
            "l.status AS l_status, l.net AS l_net FROM fade_trades f LEFT JOIN long_trades l ON l.fade_id=f.id "
            f"WHERE f.session IN ({','.join('?' * len(days))}) ORDER BY f.session DESC, f.posted_at DESC, f.id DESC "
            "LIMIT ?", (*days, limit)).fetchall()
    out = []
    for r in got:
        shorted = r["status"] in ENTERED
        longed = r["l_status"] in ENTERED
        out.append({"_t": _when(r["posted_at"]), "id": r["id"], "session": str(r["session"])[:10], "at": _et(r["posted_at"]),
                    "symbol": str(r["symbol"])[:12], "type": str(r["alert_type"] or "")[:40],
                    "short": short_words(r["status"], r["net"]) + recovered_words(r["via"], r["posted_at"], r["recovered_at"]),
                    "why": "" if shorted or r["status"] in ("pending", "ask") else why(r["reason"]),
                    "would": would_words(r["status"], r["wh_net"], r["wh_note"]),
                    "would_n": r["wh_net"] if r["status"] in would_have.NOT_TAKEN else None,
                    "long": long_words(r["l_status"], r["l_net"]), "traded": bool(shorted or longed),
                    "context": alert_context.words(r)[:160],
                    "headline": str(r["news_head"] or r["ctx_head"] or "")[:160]})
    extra = unlisted(db, channel_alerts, max_sessions, limit)
    if extra:                                              # one list, newest first within each session
        old = datetime.min.replace(tzinfo=timezone.utc)
        out = sorted(out + extra, key=lambda r: (r["session"], r["_t"] or old), reverse=True)[:limit]
    return [{k: v for k, v in r.items() if k != "_t"} for r in out]


def gates(db: OpsStore, max_sessions: int = MAX_SESSIONS, channel_alerts: list[dict] | None = None) -> list[dict]:
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
    for a in unlisted(db, channel_alerts, max_sessions, MAX_ROWS * 4):     # counted, never scored: no rule fits them
        agg.setdefault((a["session"], NOT_IN_RULES), []).append(None)
    out = []
    for (session, gate), nets in agg.items():
        x = [float(v) for v in nets if v is not None]
        out.append({"session": session, "gate": gate, "n": len(nets), "scored": len(x),
                    "avg": sum(x) / len(x) if x else None, "won": sum(v > 0 for v in x),
                    "usd": sum(x) * size if x and size is not None else None, "size": size})
    return sorted(out, key=lambda g: (g["session"], g["n"]), reverse=True)
