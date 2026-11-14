"""Midnight day summary (owner request 2026-11-05): one short, plain text per trading day.

What trades were made, how many, money made or lost, and how much was traded, for the paper fade book, the
paper approved books and real money at Schwab. Runs at 00:05 ET for the session that just ended; fade results
are official by then (fadesettle 20:40 ET), anything not yet official is labelled as an estimate.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sgt.fast import fade
from sgt.ops import chartserver
from sgt.ops.sms_style import bold
from sgt.ops.store import OpsStore

ENTERED = ("open", "est_closed", "settled")


def _usd(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def fade_day(db: OpsStore, day: date) -> dict:
    """Paper fade trades entered on ``day``: count, wins, $ result (official where settled), $ shorted."""
    base = fade.nav(db, through=day - timedelta(days=1))
    with db.tx() as c:
        rows = c.execute("SELECT symbol, status, weight, net, est_entry, est_exit FROM fade_trades WHERE session=? "
                         "AND status IN (?,?,?) ORDER BY id", (day.isoformat(), *ENTERED)).fetchall()
    trades, est = [], False
    for r in rows:
        size = (r["weight"] or fade.WEIGHT) * base
        if r["status"] == "settled" and r["net"] is not None:
            ret = r["net"]
        elif r["est_entry"] and r["est_exit"]:
            ret, est = (r["est_entry"] - r["est_exit"]) / r["est_entry"], True
        else:
            ret, est = 0.0, True                                   # still open: counted at zero, flagged
        trades.append({"symbol": r["symbol"], "usd": ret * size, "ret": ret, "size": size})
    return {"trades": trades, "n": len(trades), "wins": sum(t["usd"] > 0 for t in trades),
            "usd": sum(t["usd"] for t in trades), "traded": sum(t["size"] for t in trades), "estimate": est}


def long_day(db: OpsStore, day: date) -> dict:
    """Paper long-book trades entered on ``day`` (2026-11-14: the summary left the long book out, so 11-13 read
    -$823 here and -$1,164 in the digest). Official net where settled; a live estimate otherwise, flagged; a void
    trade (the live order never filled on the official bars) is not a trade."""
    from sgt.fast import long_book

    base = long_book.nav(db, through=day - timedelta(days=1))
    with db.tx() as c:
        rows = c.execute("SELECT symbol, status, weight, net, est_entry, est_exit FROM long_trades WHERE session=? "
                         "AND status IN (?,?,?) ORDER BY id", (day.isoformat(), *ENTERED)).fetchall()
    trades, est = [], False
    for r in rows:
        size = (r["weight"] or fade.WEIGHT) * base
        if r["status"] == "settled" and r["net"] is not None:
            ret = r["net"]
        elif r["est_entry"] and r["est_exit"]:
            ret, est = r["est_exit"] / r["est_entry"] - 1, True
        else:
            ret, est = 0.0, True                                   # still open: counted at zero, flagged
        trades.append({"symbol": r["symbol"], "usd": ret * size, "ret": ret, "size": size})
    return {"trades": trades, "n": len(trades), "wins": sum(t["usd"] > 0 for t in trades),
            "usd": sum(t["usd"] for t in trades), "traded": sum(t["size"] for t in trades), "estimate": est}


def books_day(db: OpsStore, day: date) -> list[tuple[str, float]]:
    """Paper approved books marked on ``day``: (strategy, $ change)."""
    with db.tx() as c:
        rows = c.execute("SELECT strategy, nav, ret FROM book_nav WHERE session=? ORDER BY strategy",
                         (day.isoformat(),)).fetchall()
    return [(r["strategy"], r["nav"] - r["nav"] / (1 + r["ret"])) for r in rows]


def real_day(db: OpsStore, day: date) -> dict | None:
    """Schwab pilot fills on ``day`` (ET): order count, $ traded, realized fade P&L. None if the pilot never ran."""
    from sgt.fast.fade import ET
    from sgt.pilot import fade_real
    with db.tx() as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='pilot_orders'").fetchone():
            return None
        rows = c.execute("SELECT filled_qty, avg_price, updated_at FROM pilot_orders WHERE filled_qty > 0").fetchall()
    fills = [r for r in rows if datetime.fromisoformat(r["updated_at"]).astimezone(ET).date() == day]
    return {"orders": len(fills), "traded": sum(r["filled_qty"] * r["avg_price"] for r in fills),
            "usd": fade_real.realized(db, day.isoformat())}


def build(db: OpsStore, day: date) -> str:
    f = fade_day(db, day)
    lines = [f"[SGT] 🌙 {bold(f'{day:%a %b} {day.day} summary')}"]
    if f["n"]:
        names = ", ".join(f"{t['symbol']} {_usd(t['usd'])}" for t in f["trades"])
        lines.append(f"📉 Fades (paper): {bold(str(f['n']))} trades · {f['wins']} won, {f['n'] - f['wins']} lost · "
                     f"{bold(_usd(f['usd']))} · ${f['traded']:,.0f} traded")
        lines.append(f"    {names}" + (" (some estimated)" if f["estimate"] else ""))
    else:
        lines.append("📉 Fades (paper): no trades")
    lg = long_day(db, day)
    if lg["n"]:
        names = ", ".join(f"{t['symbol']} {_usd(t['usd'])}" for t in lg["trades"])
        lines.append(f"📗 Longs (paper): {bold(str(lg['n']))} trades · {lg['wins']} won, {lg['n'] - lg['wins']} lost · "
                     f"{bold(_usd(lg['usd']))} · ${lg['traded']:,.0f} traded")
        lines.append(f"    {names}" + (" (some estimated)" if lg["estimate"] else ""))
    books = books_day(db, day)
    if books:
        from sgt.ops.digest import name

        lines.append("📦 Books (paper): " + " · ".join(f"{name(s)} {_usd(v)}" for s, v in books))
    real = real_day(db, day)
    if real and real["orders"]:
        lines.append(f"💵 {bold('REAL')} (Schwab): {real['orders']} fills · ${real['traded']:,.0f} traded · "
                     f"fades {bold(_usd(real['usd']))}")
    total = f["usd"] + lg["usd"] + sum(v for _, v in books)       # the same books the nightly digest adds up
    lines.append(f"🧾 Paper total: {bold(_usd(total))}")
    page = chartserver.link()
    if page:
        lines.append(f"📈 Charts: {page}")
    return "\n".join(lines)
