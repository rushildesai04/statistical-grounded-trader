"""The lab's daily list (owner 2026-11-17, decision 5): every stock the channel alerted in the latest sessions, with
its alerts and what the live books did, ready for ``scripts/lab.py --site``. Pure over the ops DB; no network. Symbols
and alert types come from the channel: bounded here, validated again by the script's ``spec``."""
from __future__ import annotations

from sgt.ops import alert_feed
from sgt.ops.store import OpsStore

MAX_SESSIONS, MAX_SPECS, MAX_ALERTS = 2, 60, 40


def specs(db: OpsStore, max_sessions: int = MAX_SESSIONS, limit: int = MAX_SPECS) -> list[dict]:
    """One request per (symbol, session) of the latest ``max_sessions`` sessions that have alerts: the stock's alerts
    in time order and a plain line of what the live books did with each. Stocks a book traded come first, then the
    latest alerts; at most ``limit``."""
    with db.tx() as c:
        days = [r[0] for r in c.execute("SELECT DISTINCT session FROM fade_trades ORDER BY session DESC LIMIT ?",
                                        (max_sessions,))]
        if not days:
            return []
        rows = c.execute(
            "SELECT f.symbol, f.session, f.alert_type, f.posted_at, f.status, f.reason, f.net, l.status AS l_status, "
            "l.net AS l_net FROM fade_trades f LEFT JOIN long_trades l ON l.fade_id=f.id "
            f"WHERE f.session IN ({','.join('?' * len(days))}) ORDER BY f.session DESC, f.posted_at, f.id",
            tuple(days)).fetchall()
    by: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (str(r["session"])[:10], str(r["symbol"])[:12])
        g = by.setdefault(key, {"symbol": key[1], "session": key[0], "alerts": [], "said": [], "traded": False,
                                "last": ""})
        if len(g["alerts"]) < MAX_ALERTS:
            g["alerts"].append({"at": r["posted_at"], "type": str(r["alert_type"] or "alert")[:40]})
        entered = r["status"] in alert_feed.ENTERED
        longed = r["l_status"] in alert_feed.ENTERED
        g["traded"] = g["traded"] or entered or longed
        g["last"] = max(g["last"], str(r["posted_at"]))
        short = alert_feed.short_words(r["status"], r["net"])
        why = "" if entered or r["status"] in ("pending", "ask") else alert_feed.why(r["reason"])
        line = short + (f" ({why})" if why else "")
        long = alert_feed.long_words(r["l_status"], r["l_net"])
        line += f"; {long}" if long else ""
        if line not in g["said"]:
            g["said"].append(line)
    out = sorted(by.values(), key=lambda g: (g["session"], g["traded"], g["last"]), reverse=True)[:limit]
    return [{"symbol": g["symbol"], "session": g["session"], "alerts": g["alerts"],
             "live": " | ".join(g["said"])[:300]} for g in out]
