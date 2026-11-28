"""Midnight day summary (owner request 2026-11-05): one short, plain text per trading day.

What trades were made, how many, money made or lost, and how much was traded, for the paper fade book, the
paper approved books and real money at Schwab. Runs at 00:05 ET for the session that just ended; fade results
are official by then (fadesettle 20:40 ET), anything not yet official is labelled as an estimate.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from sgt.fast import est_costs, fade
from sgt.ops import chartserver
from sgt.ops.sms_style import bold
from sgt.ops.store import OpsStore

ENTERED = ("open", "est_closed", "settled")
RECORDER_ENV = "SGT_FEED_SHADOW"            # sgt.fast.feed_shadow.ENV (pinned equal by a test)
RECORDER_BYTES, RECORDER_CHARS = 5_000_000, 900


def _usd(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.0f}"


def _along_the_book(base: float, trades: list[dict]) -> None:
    """Dollars of each trade the way the BOOK counts them (2026-11-24: the 20:50 digest said the fade lost $438 on
    11-23 and this summary $439, from the same three official results).

    A book's value compounds its settled trades one after the other (``fade.nav`` / ``long_book.nav``), so the second
    loss of a day is taken on a slightly smaller book. The summary used to take every trade on the value of the
    evening before: the same results, $0.58 apart. The book's value is the one settlement record, and the digest's day
    figure is its change, so the summary now walks the same path: official trades in the book's own order
    (``order``), each on the value the book had reached. An estimate is not in the book yet: it is taken on the value
    after the day's official trades. ``size`` stays the dollars actually put to work (the entry's own sizing)."""
    v = base
    for t in sorted((t for t in trades if t["official"]), key=lambda t: t["order"]):
        t["usd"] = v * t["weight"] * t["ret"]
        v += t["usd"]
    for t in trades:
        if not t["official"]:
            t["usd"] = v * t["weight"] * t["ret"]


def _day(trades: list[dict], est: bool) -> dict:
    return {"trades": trades, "n": len(trades), "wins": sum(t["usd"] > 0 for t in trades),
            "usd": sum(t["usd"] for t in trades), "traded": sum(t["size"] for t in trades), "estimate": est}


def fade_day(db: OpsStore, day: date) -> dict:
    """Paper fade trades entered on ``day``: count, wins, $ result (official where settled), $ shorted."""
    base = fade.nav(db, through=day - timedelta(days=1))
    with db.tx() as c:
        rows = c.execute("SELECT id, symbol, status, weight, net, settled_at, est_entry, est_entry_at, est_exit FROM "
                         "fade_trades WHERE session=? AND status IN (?,?,?) ORDER BY id", (day.isoformat(), *ENTERED)).fetchall()
    trades, est = [], False
    for r in rows:
        w = r["weight"] or fade.WEIGHT
        official = r["status"] == "settled" and r["net"] is not None
        if official:
            ret = r["net"]
        elif (n := est_costs.net("short", r["symbol"], r["est_entry_at"], r["est_entry"], r["est_exit"])) is not None:
            ret, est = n, True                                     # 2026-11-16: live estimate after estimated costs
        else:
            ret, est = 0.0, True                                   # still open: counted at zero, flagged
        trades.append({"symbol": r["symbol"], "ret": ret, "size": w * base, "weight": w, "official": official,
                       "order": (r["settled_at"] or "", r["id"])})     # fade.nav: ORDER BY settled_at, id
    _along_the_book(base, trades)
    return _day(trades, est)


def long_day(db: OpsStore, day: date) -> dict:
    """Paper long-book trades entered on ``day`` (2026-11-14: the summary left the long book out, so 11-13 read
    -$823 here and -$1,164 in the digest). Official net where settled; a live estimate otherwise, flagged; a void
    trade (the live order never filled on the official bars) is not a trade."""
    from sgt.fast import long_book

    base = long_book.nav(db, through=day - timedelta(days=1))
    with db.tx() as c:
        rows = c.execute("SELECT id, symbol, status, weight, net, entry_ts, est_entry, est_exit FROM long_trades WHERE "
                         "session=? AND status IN (?,?,?) ORDER BY id", (day.isoformat(), *ENTERED)).fetchall()
    trades, est = [], False
    for r in rows:
        w = r["weight"] or fade.WEIGHT
        official = r["status"] == "settled" and r["net"] is not None
        if official:
            ret = r["net"]
        elif (n := est_costs.net("long", r["symbol"], r["entry_ts"], r["est_entry"], r["est_exit"])) is not None:
            ret, est = n, True
        else:
            ret, est = 0.0, True                                   # still open: counted at zero, flagged
        trades.append({"symbol": r["symbol"], "ret": ret, "size": w * base, "weight": w, "official": official,
                       "order": r["id"]})                              # long_book.nav: ORDER BY id
    _along_the_book(base, trades)
    return _day(trades, est)


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


def recorder_line(db: OpsStore, day: date) -> str:
    """The feed recorder's frozen check for ``day``, as its nightly job wrote it at 21:40 ET (``report`` in the
    session's line of ``reports/feed_shadow/daily.jsonl``, ``scripts/feed_shadow.py``): owner 2026-11-27, the result
    is delivered in this text and not only in a file somebody has to read. "" only where the recorder is not
    switched on and its job never wrote a line. A missing line or a line without the check is said as "not
    measured", never left out and never read as a pass. The text comes from our own job's file: read as data, one
    line, bounded in size. Whenever the recorder's OFF file exists the line says so, whatever the job wrote: a
    recorder that is off is never shown as running (the nightly job creates that file on a confirmed breach)."""
    p = Path(db.path).resolve().parent.parent / "feed_shadow" / "daily.jsonl"
    head = f"📡 feed recorder {day.isoformat()}: not measured: "
    off = " [OFF file present: the recorder is off until a person removes it]" if (p.parent / "OFF").exists() else ""
    if not p.exists():
        return (head + "its nightly job has written no line" + off) if os.environ.get(RECORDER_ENV) == "1" or off else ""
    if p.stat().st_size > RECORDER_BYTES:
        return head + "its nightly file is too large to read" + off
    last = None
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if isinstance(r, dict) and r.get("session") == day.isoformat():
            last = r
    if last is None:
        return head + "its nightly job wrote no line for this session" + off
    said = last.get("report")
    if not isinstance(said, str) or not said.strip():
        return head + "the session's line carries no check (written before the check was deployed)" + off
    return "📡 " + " ".join(said.split())[:RECORDER_CHARS] + off


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
    from sgt.ops.digest import NOT_COUNTED, group, name

    counted = [(s, v) for s, v in books if group(s) not in NOT_COUNTED]
    if counted:
        lines.append("📦 Books (paper): " + " · ".join(f"{name(s)} {_usd(v)}" for s, v in counted))
    for g, label in NOT_COUNTED.items():             # owner 2026-11-14: shown, not added to the total
        rows = [(s, v) for s, v in books if group(s) == g]
        if rows:
            lines.append(f"📐 {label.capitalize()} (not in the total): "
                         + " · ".join(f"{name(s)} {_usd(v)}" for s, v in rows))
    real = real_day(db, day)
    if real and real["orders"]:
        lines.append(f"💵 {bold('REAL')} (Schwab): {real['orders']} fills · ${real['traded']:,.0f} traded · "
                     f"fades {bold(_usd(real['usd']))}")
    try:                                        # owner 2026-11-24: a texted estimate the settle moved far, by name
        from sgt.ops import revisions

        rv = revisions.short_note(db, day)
        if rv:
            lines.append(rv)
    except Exception:  # noqa: BLE001 - one informational line; said, never silent
        lines.append("✏️ Revisions: unavailable")
    try:                                        # owner 2026-11-27: the recorder's frozen check, in the report itself
        rl = recorder_line(db, day)
        if rl:
            lines.append(rl)
    except Exception:  # noqa: BLE001 - one informational line; said, never silent and never a pass
        lines.append("📡 feed recorder: not measured: the check could not be read")
    total = f["usd"] + lg["usd"] + sum(v for _, v in counted)     # the same books the nightly digest adds up
    lines.append(f"🧾 Paper total (trading books): {bold(_usd(total))}")
    page = chartserver.link()
    if page:
        lines.append(f"📈 Charts: {page}")
    return "\n".join(lines)
