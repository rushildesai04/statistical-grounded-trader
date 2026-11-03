"""Live alert-fade book (2026-11-03; rule frozen in docs/findings/prereg_alert_fade_2026-11-01.md).

Two layers, so the live book can never drift from the tested rule:

- LIVE (this module): an alert arrives -> research filters (cell type, 09:30-15:44 ET entry, $1-50, first
  event per symbol-session) -> ops gates (ARM for today, not paused, daily caps) -> a virtual short at the
  real-time (IEX) price, texted. A per-minute monitor texts the estimated stop (+20%) or 60-minute exit.
- OFFICIAL (``settle``): once SIP 1-minute bars are final, the caller's ``official`` function re-computes
  every trade with the research code itself (telegram_alert_test.attach_trades/primary_events +
  alert_fade_test.score). Only official nets move the book's NAV; estimates are display-only.

Status: filtered (a research rule excluded it; does not block a later alert) | skipped (an ops gate
blocked the research event) | pending (waiting for a first quote) | open | est_closed | settled | void.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

from sgt.intraday.calendar import default_calendar
from sgt.ops import approvals
from sgt.ops.store import OpsStore, iso, utcnow

ET = ZoneInfo("America/New_York")
# Mirrors telegram_alert_test.CELL_OF (cells only); tests/fast/test_fade.py asserts they stay equal.
CELLS = frozenset({"volume_increasing", "volume_spike", "gaining_volume", "gaining_momentum", "new_high",
                   "intraday_high", "testing_high", "price_level", "resumed", "news"})
REACTION = timedelta(seconds=10)
MAX_ENTRY_DELAY = timedelta(minutes=5)
FIRST_ENTRY, LAST_ENTRY = time(9, 30), time(15, 44)
PRICE_MIN, PRICE_MAX = 1.0, 50.0
STOP, HOLD = 0.20, timedelta(minutes=60)
SETTLE_AFTER = timedelta(minutes=60 + 20)       # SIP is free once 15 min old; 5 min margin
NAV0 = 100_000.0
WEIGHT = 0.05                                   # short notional per trade, share of NAV
MAX_TRADES_DAY, MAX_OPEN = 15, 8
DAY_LOSS_STOP = -0.03                           # NAV fraction (estimates) that stops new entries for the day
HEARTBEAT_STALE = timedelta(minutes=15)

Quote = Callable[[str], "float | None"]
Official = Callable[[pd.DataFrame], pd.DataFrame]


@dataclass(frozen=True, slots=True)
class LiveAlert:
    symbol: str
    alert_type: str
    posted_at: datetime                         # tz-aware
    channel: str
    message_id: int
    has_news: bool | None = None


def _insert(db: OpsStore, a: LiveAlert, session: str, status: str, reason: str, now: datetime) -> int | None:
    with db.tx() as c:
        if status != "filtered" and c.execute("SELECT 1 FROM fade_trades WHERE symbol=? AND session=? AND "
                                              "status!='filtered'", (a.symbol, session)).fetchone():
            return None
        return c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status,"
                         " reason, weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (a.symbol, session, a.alert_type, iso(a.posted_at), a.channel, a.message_id, status, reason,
                          WEIGHT if status == "pending" else None, iso(now))).lastrowid


def day_est_pnl(db: OpsStore, session: str) -> float:
    """Estimated NAV fraction realized today (closed estimates; open trades at their last mark)."""
    with db.tx() as c:
        rows = c.execute("SELECT weight, est_entry, COALESCE(est_exit, est_last) px FROM fade_trades WHERE session=? "
                         "AND status IN ('open','est_closed') AND est_entry > 0", (session,)).fetchall()
    return sum(r["weight"] * (r["est_entry"] - r["px"]) / r["est_entry"] for r in rows if r["px"])


def gate(db: OpsStore, session: str, now: datetime) -> str | None:
    """Ops reason that blocks a new entry, or None."""
    if not approvals.armed(db, now):
        return "paused" if db.paused() else "not armed"
    with db.tx() as c:
        n_day = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session=? AND status IN "
                          "('pending','open','est_closed','settled','void')", (session,)).fetchone()[0]
        n_open = c.execute("SELECT COUNT(*) FROM fade_trades WHERE status IN ('pending','open')").fetchone()[0]
    if n_day >= MAX_TRADES_DAY:
        return f"daily cap {MAX_TRADES_DAY}"
    if n_open >= MAX_OPEN:
        return f"open cap {MAX_OPEN}"
    if day_est_pnl(db, session) <= DAY_LOSS_STOP:
        return f"day loss stop {DAY_LOSS_STOP:.0%}"
    return None


def on_alert(db: OpsStore, a: LiveAlert, now: datetime | None = None) -> str:
    """Classify one parsed alert. Returns what happened (for logs); texts nothing (``monitor`` does)."""
    now = now or utcnow()
    if a.posted_at.tzinfo is None:
        raise ValueError("naive posted_at")
    if a.alert_type not in CELLS or (a.alert_type == "news" and a.has_news is not True):
        return "not a cell"
    et = a.posted_at.astimezone(ET)
    session = et.date().isoformat()
    if not default_calendar().is_session(et.date()):
        return "not a session"
    entry_et = (a.posted_at + REACTION).astimezone(ET).time()
    if not FIRST_ENTRY <= entry_et <= LAST_ENTRY:
        _insert(db, a, session, "filtered", "outside 09:30-15:44", now)
        return "filtered: window"
    if now - a.posted_at > MAX_ENTRY_DELAY:
        _insert(db, a, session, "skipped", "alert received late", now)
        return "skipped: late"
    reason = gate(db, session, now)
    rid = _insert(db, a, session, "skipped" if reason else "pending", reason or "", now)
    if rid is None:
        return "duplicate symbol-session"
    return f"skipped: {reason}" if reason else f"pending #{rid}"


def _fmt(px: float | None) -> str:
    return "n/a" if px is None else (f"{px:.2f}" if px >= 1 else f"{px:.4f}")


def monitor(db: OpsStore, send, quote: Quote, now: datetime | None = None) -> None:
    """Advance live estimates: pending -> open (first quote, price filter), open -> est_closed (stop / 60 min)."""
    now = now or utcnow()
    db.set_flag("fade_heartbeat", iso(now))
    with db.tx() as c:
        rows = c.execute("SELECT * FROM fade_trades WHERE status IN ('pending','open') ORDER BY id").fetchall()
    for r in rows:
        posted = datetime.fromisoformat(r["posted_at"])
        if r["status"] == "pending":
            if now < posted + REACTION:
                continue
            px = quote(r["symbol"])
            if px is None or not px > 0:
                if now - posted > MAX_ENTRY_DELAY:
                    _set(db, r["id"], "pending", status="skipped", reason="no quote within 5 min")
                continue
            if not PRICE_MIN <= px <= PRICE_MAX:
                _set(db, r["id"], "pending", status="filtered", reason=f"price {_fmt(px)} outside $1-50")
                continue
            if _set(db, r["id"], "pending", status="open", est_entry=px, est_last=px, est_entry_at=iso(now)):
                out = (now + HOLD).astimezone(ET)
                send(f"[SGT] fade SHORT {r['symbol']} ~${_fmt(px)} est ({r['alert_type']}); stop ${_fmt(px * (1 + STOP))},"
                     f" out by {out:%H:%M} ET. {WEIGHT:.0%} of fade book.")
            continue
        px = quote(r["symbol"])
        entry, started = r["est_entry"], datetime.fromisoformat(r["est_entry_at"])
        if px is not None and px > 0:
            _set(db, r["id"], "open", est_last=px)
        last = px if px is not None and px > 0 else r["est_last"]
        why = "stop" if last and last >= entry * (1 + STOP) else ("60 min" if now >= started + HOLD else None)
        if why is None:
            continue
        exit_px = max(last, entry * (1 + STOP)) if why == "stop" else last
        if _set(db, r["id"], "open", status="est_closed", est_exit=exit_px, est_exit_at=iso(now), reason=why):
            g = (entry - exit_px) / entry
            send(f"[SGT] fade COVER {r['symbol']} ~${_fmt(exit_px)} est ({why}): {g:+.1%} before costs. "
                 "Official result after SIP bars (~20 min).")


def _set(db: OpsStore, rid: int, expect: str, **cols) -> bool:
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:
        return c.execute(f"UPDATE fade_trades SET {keys} WHERE id=? AND status=?",  # noqa: S608 - fixed column names
                         (*cols.values(), rid, expect)).rowcount == 1


def due(db: OpsStore, now: datetime | None = None) -> pd.DataFrame:
    """Trades whose official 60-minute window is final on SIP (``SETTLE_AFTER`` past the alert)."""
    now = now or utcnow()
    with db.tx() as c:
        rows = c.execute("SELECT * FROM fade_trades WHERE status IN ('open','est_closed') AND posted_at <= ? ORDER BY id",
                         (iso(now - SETTLE_AFTER),)).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def settle(db: OpsStore, official: Official, now: datetime | None = None) -> list[str]:
    """Book official results. ``official(due_rows)`` returns per ``id``: qualifies (bool), entry_ts, entry, cover,
    cost_rt, gross, net (research code). Non-qualifying events (e.g. official entry outside $1-50) are void."""
    now = now or utcnow()
    d = due(db, now)
    if d.empty:
        return []
    res = official(d).set_index("id")
    lines = []
    for r in d.itertuples(index=False):
        if r.id not in res.index:
            continue                                         # bars not available yet: try next run
        o = res.loc[r.id]
        if not bool(o["qualifies"]) or pd.isna(o["net"]):
            _set(db, r.id, r.status, status="void", reason=str(o.get("why", "not a research event")), settled_at=iso(now))
            lines.append(f"{r.symbol} void")
            continue
        if _set(db, r.id, r.status, status="settled", entry_ts=str(o["entry_ts"]), entry=float(o["entry"]),
                cover=float(o["cover"]), cost_rt=float(o["cost_rt"]), gross=float(o["gross"]), net=float(o["net"]),
                settled_at=iso(now)):
            lines.append(f"{r.symbol} {float(o['net']):+.2%}")
    return lines


def nav(db: OpsStore, through: date | None = None) -> float:
    with db.tx() as c:
        rows = c.execute("SELECT weight, net FROM fade_trades WHERE status='settled'" +
                         (" AND session<=?" if through else "") + " ORDER BY settled_at, id",
                         ((through.isoformat(),) if through else ())).fetchall()
    v = NAV0
    for r in rows:
        v *= 1 + r["weight"] * r["net"]
    return v


def digest_lines(db: OpsStore, day: date, now: datetime | None = None) -> list[str]:
    s = day.isoformat()
    beat = db.flag("fade_heartbeat")
    down = beat and (now or utcnow()) - datetime.fromisoformat(beat) > HEARTBEAT_STALE
    warn = [f"! fade listener DOWN since {datetime.fromisoformat(beat).astimezone(ET):%m-%d %H:%M} ET"] if down else []
    with db.tx() as c:
        rows = c.execute("SELECT status, net FROM fade_trades WHERE session=?", (s,)).fetchall()
        n_all = c.execute("SELECT COUNT(*) FROM fade_trades WHERE status='settled'").fetchone()[0]
    if not rows and not n_all:
        return warn
    st = [r for r in rows if r["status"] == "settled"]
    sk = sum(r["status"] == "skipped" for r in rows)
    wait = sum(r["status"] in ("pending", "open", "est_closed") for r in rows)
    day_net = sum(r["net"] for r in st) / len(st) if st else None
    v = nav(db)
    line = f"fade: ${v:,.0f} ({v / NAV0 - 1:+.2%} since start, {n_all} trades)"
    line += f"; today {len(st)} settled" + (f" avg {day_net:+.2%}/trade" if st else "")
    line += (f", {sk} skipped" if sk else "") + (f", {wait} unsettled" if wait else "")
    return [line] + warn
