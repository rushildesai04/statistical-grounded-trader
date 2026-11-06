"""Live alert-fade book (2026-11-03; rule frozen in docs/findings/prereg_alert_fade_2026-11-01.md).

Two layers, so the live book can never drift from the tested rule:

- LIVE (this module): an alert arrives -> research filters (cell type, 09:30-15:44 ET entry, $1-50, first
  event per symbol-session) -> ops gates (ARM for today, not paused, daily caps) -> a virtual short at the
  real-time (IEX) price, texted. A per-minute monitor texts the estimated stop (+20%) or 60-minute exit.
- OFFICIAL (``settle``): once SIP 1-minute bars are final, the caller's ``official`` function re-computes
  every trade with the research code itself (telegram_alert_test.attach_trades/primary_events +
  alert_fade_test.score). Only official nets move the book's NAV; estimates are display-only.

Entry permission (amendments F3/F4): ARMed day -> automatic; otherwise each event texts a request that enters
automatically at its deadline unless the owner replies NO (OK enters at once); the deadline keeps the entry inside
the tested 5-minute window; PAUSE / DISARM block it. Approved and auto-entered trades are officially scored from
their real entry time. Exits are always automatic.

Status: filtered (a research rule excluded it; does not block a later alert) | skipped (an ops gate
blocked the research event) | ask (approval texted, waiting) | pending (waiting for a first quote) | open |
est_closed | settled | void.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable

import pandas as pd

from sgt.fast.fade_rule import (AVG_LOSS, AVG_WIN, BASE_EV, BASE_N, BASE_WIN, BASE_WIN_CI, ET, HOLD,  # noqa: F401
                                JUDGE_N, STOP, STOP_RATE, STOPPED_NET, VETO, VETO_EXIT)
from sgt.fast.fade_texts import (CONF, CONF_LABEL, CONF_MAX, EXIT_WHY, FILING_HIST, PLAIN, TOD_HIST, _fmt,  # noqa: F401
                                 _qty, _usd, ask_text, borrow_note, confidence, news_group, out_text, signals_line,
                                 tod_hist)
from sgt.intraday.calendar import default_calendar
from sgt.ops import approvals
from sgt.ops.sms_style import italic
from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
# Mirrors telegram_alert_test.CELL_OF (cells only); tests/fast/test_fade.py asserts they stay equal.
CELLS = frozenset({"volume_increasing", "volume_spike", "gaining_volume", "gaining_momentum", "new_high",
                   "intraday_high", "testing_high", "price_level", "resumed", "news"})
REACTION = timedelta(seconds=10)
MAX_ENTRY_DELAY = timedelta(minutes=5)
FIRST_ENTRY, LAST_ENTRY = time(9, 30), time(15, 44)
PRICE_MIN, PRICE_MAX = 1.0, 50.0
SETTLE_AFTER = timedelta(minutes=60 + 20)       # SIP is free once 15 min old; 5 min margin
NAV0 = 100_000.0
WEIGHT = 0.05                                   # short notional per trade, share of NAV
MAX_TRADES_DAY, MAX_OPEN = 15, 8
DAY_LOSS_STOP = -0.03                           # NAV fraction (estimates) that stops new entries for the day
HEARTBEAT_STALE = timedelta(minutes=15)
ASK_MARGIN = timedelta(seconds=40)              # approval must land this long before the entry deadline
# Amendment F5 (owner decision 2026-11-04, prereg_fade_challengers: entering 2-4 min late loses most of the edge):
# not ARMed -> enter at the tested timing and text the trade with "NO <code> = exit now". True restores the
# F3/F4 ask-first flow (_ask), kept intact for rows created before F5 and for a reversal.
ASK_FIRST = False

Quote = Callable[[str], "float | None"]
Borrow = Callable[[str, datetime], dict]
BORROW_COLS = ("ib_avail", "ib_fee", "ib_snap", "alp_etb", "alp_shortable")
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
                          WEIGHT if status in ("pending", "ask") else None, iso(now))).lastrowid


def day_est_pnl(db: OpsStore, session: str) -> float:
    """Estimated NAV fraction realized today (closed estimates; open trades at their last mark)."""
    with db.tx() as c:
        rows = c.execute("SELECT weight, est_entry, COALESCE(est_exit, est_last) px FROM fade_trades WHERE session=? "
                         "AND status IN ('open','est_closed') AND est_entry > 0", (session,)).fetchall()
    return sum(r["weight"] * (r["est_entry"] - r["px"]) / r["est_entry"] for r in rows if r["px"])


def gate(db: OpsStore, session: str, now: datetime) -> str | None:
    """Ops reason that blocks a new AUTOMATIC entry, or None."""
    if not approvals.armed(db, now):
        return "paused" if db.paused() else "not armed"
    return caps(db, session)


def caps(db: OpsStore, session: str) -> str | None:
    """Risk caps that block any new entry (automatic or approved), or None."""
    with db.tx() as c:
        n_day = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session=? AND status IN "
                          "('ask','pending','open','est_closed','settled','void')", (session,)).fetchone()[0]
        n_open = c.execute("SELECT COUNT(*) FROM fade_trades WHERE status IN ('ask','pending','open')").fetchone()[0]
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
    if db.paused():
        reason, status = "paused", "skipped"
    elif (reason := caps(db, session)) is not None:
        status = "skipped"
    elif approvals.armed(db, now):
        status, reason = "pending", ""
    elif ASK_FIRST:
        status, reason = "ask", "awaiting approval"
    else:
        status, reason = "pending", VETO
    rid = _insert(db, a, session, status, reason, now)
    if rid is None:
        return "duplicate symbol-session"
    return f"skipped: {reason}" if status == "skipped" else f"{status} #{rid}"


def monitor(db: OpsStore, send, quote: Quote, now: datetime | None = None, borrow: Borrow | None = None,
            context: Callable[[str], dict] | None = None) -> None:
    """Advance live estimates: ask -> pending (approval), pending -> open (first quote, price filter),
    open -> est_closed (stop / 60 min). ``borrow(symbol, now)`` stamps IBKR/Alpaca borrow facts once per event."""
    now = now or utcnow()
    db.set_flag("fade_heartbeat", iso(now))
    with db.tx() as c:
        rows = c.execute("SELECT * FROM fade_trades WHERE status IN ('ask','pending','open') ORDER BY id").fetchall()
    for r in rows:
        try:
            if borrow is not None and r["ib_snap"] is None and r["alp_etb"] is None and r["status"] != "open":
                r = _stamp(db, r, borrow(r["symbol"], now))
            _advance(db, send, quote, r, now, context)
        except Exception:  # noqa: BLE001 - one bad row must not stall the book; logged with its id
            log.exception("fade row %s failed", r["id"])


def _stamp(db: OpsStore, r, facts: dict) -> dict:
    """Record borrow facts at alert time (plan 2026-11-04 W1). Only known fields are written."""
    cols = {k: v for k, v in facts.items() if k in BORROW_COLS and v is not None}
    if cols:
        keys = ", ".join(f"{k}=?" for k in cols)
        with db.tx() as c:
            c.execute(f"UPDATE fade_trades SET {keys} WHERE id=?", (*cols.values(), r["id"]))  # noqa: S608 - fixed names
    return {**dict(r), **cols}


def record(db: OpsStore) -> tuple[int, float | None]:
    """Settled live-book trades and their mean official net."""
    with db.tx() as c:
        n, mean = c.execute("SELECT COUNT(*), AVG(net) FROM fade_trades WHERE status='settled'").fetchone()
    return int(n), mean


def _advance(db: OpsStore, send, quote: Quote, r, now: datetime, context=None) -> None:
    if r["status"] == "ask":
        _ask(db, send, quote, r, now, context)
    elif r["status"] == "pending":
        _enter(db, send, quote, r, now, context)
    else:
        _exit(db, send, quote, r, now)


def _approval(db: OpsStore, rid: int):
    with db.tx() as c:
        return c.execute("SELECT * FROM approvals WHERE strategy=? AND kind='fade' ORDER BY id DESC LIMIT 1",
                         (f"fade:{rid}",)).fetchone()


def _ask(db: OpsStore, send, quote: Quote, r, now: datetime, context=None) -> None:
    """Text one approval request per event; an approved request becomes a pending entry (same tick)."""
    posted = datetime.fromisoformat(r["posted_at"])
    deadline = posted + MAX_ENTRY_DELAY - ASK_MARGIN
    a = _approval(db, r["id"])
    if a is None:
        if now >= deadline:
            _set(db, r["id"], "ask", status="skipped", reason="no time left to ask")
            return
        px = quote(r["symbol"])
        if px is not None and px > 0 and not PRICE_MIN <= px <= PRICE_MAX:
            _set(db, r["id"], "ask", status="filtered", reason=f"price {_fmt(px)} outside $1-50")
            return
        ctx = _context(db, r, context)
        summary = ask_text(r["symbol"], r["alert_type"], px, notional(db), ctx, borrow_note(r), record(db), at=now)
        aid = approvals.request(db, send, strategy=f"fade:{r['id']}", kind="fade", period=r["session"],
                                summary=summary, payload={"fade_id": r["id"]}, expires_at=deadline, now=now)
        if aid is None:
            _set(db, r["id"], "ask", status="skipped", reason="approval not sent (paused or SMS failed)")
        return
    if a["status"] == "approved":
        if not approvals.claim(db, a["id"], now):               # expired between the reply and this tick
            _set(db, r["id"], "ask", status="skipped", reason="approval expired before entry")
            return
        if db.paused() or now > deadline + ASK_MARGIN:
            approvals.finish(db, a["id"], False, "paused or too late")
            _set(db, r["id"], "ask", status="skipped", reason="paused or too late after approval")
            return
        approvals.finish(db, a["id"], True, "entering")
        if _set(db, r["id"], "ask", status="pending", reason="approved"):
            _enter(db, send, quote, {**dict(r), "status": "pending", "reason": "approved"}, now, context)
        return
    if a["status"] == "rejected":
        _set(db, r["id"], "ask", status="skipped", reason="declined")
        return
    if a["status"] in ("failed", "superseded") or (a["status"] == "expired" and a["result"] in ("paused", "lockout")):
        _set(db, r["id"], "ask", status="skipped", reason=f"approval {a['result'] or a['status']}")
        return
    if now < deadline:
        return                                        # still waiting for a reply
    # Opt-out (owner decision 2026-11-04): silence at the deadline = enter, unless PAUSE / DISARM.
    if approvals.disarmed(db, now):
        _set(db, r["id"], "ask", status="skipped", reason="paused or disarmed before auto-entry")
        return
    if now > deadline + ASK_MARGIN:                    # listener was down past the tested entry window
        _set(db, r["id"], "ask", status="skipped", reason="auto-entry window missed")
        return
    with db.tx() as c:                                 # claim the silent request exactly once
        won = c.execute("UPDATE approvals SET status='executed', decided_at=?, result='auto-entered at expiry "
                        "(no reply)' WHERE id=? AND (status='pending' OR (status='expired' AND result IS NULL))",
                        (iso(now), a["id"])).rowcount
    if not won:                                        # someone else decided it (e.g. PAUSE): never enter
        _set(db, r["id"], "ask", status="skipped", reason="approval no longer open")
    elif _set(db, r["id"], "ask", status="pending", reason="auto"):
        _enter(db, send, quote, {**dict(r), "status": "pending", "reason": "auto"}, now, context)


def _enter(db: OpsStore, send, quote: Quote, r, now: datetime, context=None) -> None:
    posted = datetime.fromisoformat(r["posted_at"])
    if now < posted + REACTION:
        return
    allowed = approvals.armed(db, now) or (r["reason"] in ("approved", "auto", VETO) and not approvals.disarmed(db, now))
    if not allowed:                                   # DISARM / PAUSE after the alert cancels the entry
        _set(db, r["id"], "pending", status="skipped", reason="disarmed before entry")
        return
    px = quote(r["symbol"])
    if px is None or not px > 0:
        if now - posted > MAX_ENTRY_DELAY:
            _set(db, r["id"], "pending", status="skipped", reason="no quote within 5 min")
        return
    if not PRICE_MIN <= px <= PRICE_MAX:
        _set(db, r["id"], "pending", status="filtered", reason=f"price {_fmt(px)} outside $1-50")
        return
    if _set(db, r["id"], "pending", status="open", est_entry=px, est_last=px, est_entry_at=iso(now)):
        out = _exit_at(r["session"], now)[0].astimezone(ET)
        if r["reason"] == VETO:
            _veto_text(db, send, r, px, now, out, context)
            return
        ctx = _context(db, r, context)
        how = " " + italic("(auto: no reply in time)") if r["reason"] == "auto" else ""
        send("[SGT] " + ask_text(r["symbol"], r["alert_type"], px, notional(db), ctx, borrow_note(r), record(db),
                                 out_at=out, at=now) + how)


def _context(db: OpsStore, r, context) -> dict:
    """Tape + company news for the trade text (informational: a failure never blocks the text or the trade). The
    news flags and the confidence shown are stored on the trade for the OUT text and the forward check."""
    if context is None:
        return {}
    try:
        ctx = context(r["symbol"]) or {}
    except Exception:  # noqa: BLE001 - context is informational; the text still goes out
        log.warning("context %s failed", r["symbol"])
        ctx = {}
    n = ctx.get("news")
    cols = {"conf": confidence(r["alert_type"], ctx, datetime.fromisoformat(r["posted_at"]))[0]}   # v2, A2
    if n:
        cols.update(news_own=int(bool(n.get("news_own"))), news_earn=int(bool(n.get("news_earn"))),
                    news_head=n.get("headline"))
    fl = ctx.get("filings")
    if fl:
        cols.update(filing_dilution=int(bool(fl.get("dilution_30d"))), filing_form=fl.get("dilution_form"),
                    filing_8k=int(bool(fl.get("eightk_today"))))
    st = ctx.get("social")
    if st:
        cols.update(st_n60=st.get("st_n60"), st_n15=st.get("st_n15"), st_span=st.get("st_span_min"),
                    st_bull=st.get("st_bull"), st_bear=st.get("st_bear"), st_watch=st.get("st_watch"))
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:
        c.execute(f"UPDATE fade_trades SET {keys} WHERE id=?", (*cols.values(), r["id"]))  # noqa: S608 - fixed names
    return ctx


def _veto_text(db: OpsStore, send, r, px: float, now: datetime, out: datetime, context) -> None:
    """F5 entry text: the full trade card + "NO <code> = exit now" (approval kind 'fadeexit', open until the exit)."""
    ctx = _context(db, r, context)
    body = ask_text(r["symbol"], r["alert_type"], px, notional(db), ctx, borrow_note(r), record(db), out_at=out,
                    at=now)
    approvals.request(db, send, strategy=f"fade:{r['id']}", kind="fadeexit", period=r["session"], summary=body,
                      payload={"fade_id": r["id"], "symbol": r["symbol"]}, expires_at=out, now=now)


def _vetoed(db: OpsStore, rid: int) -> bool:
    with db.tx() as c:
        return c.execute("SELECT 1 FROM approvals WHERE strategy=? AND kind='fadeexit' AND status='rejected'",
                         (f"fade:{rid}",)).fetchone() is not None


def _exit_at(session: str, started: datetime) -> tuple[datetime, str]:
    """Time-exit of a live trade: 60 minutes, or the regular-session close if earlier (the research window holds
    only RTH bars, so a late entry is covered at the last RTH bar; plan W5 2026-11-04)."""
    close = default_calendar().bounds(date.fromisoformat(session)).rth_close
    return (started + HOLD, "60 min") if started + HOLD <= close else (close, "close")


def _exit(db: OpsStore, send, quote: Quote, r, now: datetime) -> None:
    px = quote(r["symbol"])
    entry, started = r["est_entry"], datetime.fromisoformat(r["est_entry_at"])
    if px is not None and px > 0:
        _set(db, r["id"], "open", est_last=px)
    last = px if px is not None and px > 0 else r["est_last"]
    end, why_end = _exit_at(r["session"], started)
    vetoed = r["reason"] == VETO and _vetoed(db, r["id"])
    why = "stop" if last and last >= entry * (1 + STOP) else (VETO_EXIT if vetoed else (why_end if now >= end else None))
    if why is None:
        return
    exit_px = max(last, entry * (1 + STOP)) if why == "stop" else last
    if _set(db, r["id"], "open", status="est_closed", est_exit=exit_px, est_exit_at=iso(now), reason=why):
        with db.tx() as c:                    # a NO after the exit is answered "already closed", never acted on
            c.execute("UPDATE approvals SET status='expired', decided_at=?, result='trade closed' WHERE strategy=? "
                      "AND kind='fadeexit' AND status='pending'", (iso(now), f"fade:{r['id']}"))
        g = (entry - exit_px) / entry
        v = nav(db)
        usd = g * (r["weight"] or WEIGHT) * v
        with db.tx() as c:
            n_day = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session=? AND status='est_closed'",
                              (r["session"],)).fetchone()[0]
        send(out_text(r, entry, exit_px, why, usd, day_est_pnl(db, r["session"]) * v, n_day))


def _set(db: OpsStore, rid: int, expect: str, **cols) -> bool:
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:
        return c.execute(f"UPDATE fade_trades SET {keys} WHERE id=? AND status=?",  # noqa: S608 - fixed column names
                         (*cols.values(), rid, expect)).rowcount == 1


def due(db: OpsStore, now: datetime | None = None) -> pd.DataFrame:
    """Trades whose official 60-minute window is final on SIP (``SETTLE_AFTER`` past the alert)."""
    now = now or utcnow()
    with db.tx() as c:
        rows = c.execute("SELECT f.*, CASE WHEN a.id IS NOT NULL THEN f.est_entry_at END AS entry_after "
                         "FROM fade_trades f LEFT JOIN approvals a ON a.strategy = 'fade:' || f.id AND a.kind='fade' "
                         "AND a.status='executed' WHERE f.status IN ('open','est_closed') AND f.est_entry_at <= ? "
                         "ORDER BY f.id", (iso(now - SETTLE_AFTER),)).fetchall()
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
        rule = {k: float(o[k]) for k in ("rule_entry", "rule_net") if k in o and pd.notna(o[k])}
        if _set(db, r.id, r.status, status="settled", entry_ts=str(o["entry_ts"]), entry=float(o["entry"]),
                cover=float(o["cover"]), cost_rt=float(o["cost_rt"]), gross=float(o["gross"]), net=float(o["net"]),
                settled_at=iso(now), **rule):
            lines.append(f"{r.symbol} {float(o['net']):+.2%}")
    return lines


def notional(db: OpsStore) -> float:
    """Dollar size of one new fade trade: WEIGHT x current official book value."""
    return WEIGHT * nav(db)


def nav(db: OpsStore, through: date | None = None) -> float:
    with db.tx() as c:
        rows = c.execute("SELECT weight, net FROM fade_trades WHERE status='settled'" +
                         (" AND session<=?" if through else "") + " ORDER BY settled_at, id",
                         ((through.isoformat(),) if through else ())).fetchall()
    v = NAV0
    for r in rows:
        v *= 1 + r["weight"] * r["net"]
    return v


CHANNEL_QUIET = 0.25                            # alerts today below this share of the usual day -> warn


def channel_health(db: OpsStore, session: str) -> list[str]:
    """Warn when the channel went quiet (self-fade study 2026-11-06: the edge is the channel's selection, so a silent
    or changed channel, or a listener that stopped receiving it, must show the same night). Needs 5+ past sessions."""
    with db.tx() as c:
        today = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session=?", (session,)).fetchone()[0]
        past = [r[0] for r in c.execute("SELECT COUNT(*) FROM fade_trades WHERE session<? GROUP BY session "
                                        "ORDER BY session DESC LIMIT 20", (session,))]
    if len(past) < 5:
        return []
    usual = sorted(past)[len(past) // 2]
    if usual and today < CHANNEL_QUIET * usual:
        return [f"! channel quiet: {today} alerts today vs usual {usual} (listener or channel change?)"]
    return []


def digest_lines(db: OpsStore, day: date, now: datetime | None = None) -> list[str]:
    s = day.isoformat()
    beat = db.flag("fade_heartbeat")
    down = beat and (now or utcnow()) - datetime.fromisoformat(beat) > HEARTBEAT_STALE
    warn = [f"! fade listener DOWN since {datetime.fromisoformat(beat).astimezone(ET):%m-%d %H:%M} ET"] if down else []
    warn += channel_health(db, s)
    with db.tx() as c:
        rows = c.execute("SELECT status, net FROM fade_trades WHERE session=?", (s,)).fetchall()
        n_all = c.execute("SELECT COUNT(*) FROM fade_trades WHERE status='settled'").fetchone()[0]
        ib = c.execute("SELECT COUNT(*), AVG(net) FROM fade_trades WHERE status='settled' AND ib_avail > 0").fetchone()
        # RAYA review 2026-11-06: news-type alerts or company news earned ~0 historically; owner kept trading them,
        # so the live record shows the split nightly for the n >= 300 decision.
        nw = c.execute("SELECT (alert_type='news' OR news_own=1) AS nw, COUNT(*), AVG(net) FROM fade_trades "
                       "WHERE status='settled' GROUP BY nw").fetchall()
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
    if n_all:                                 # the subset that could really be shorted (IBKR had shares)
        line += f"; IBKR-borrowable {ib[0]} trades" + (f" avg {ib[1]:+.2%}" if ib[0] else "")
        split = {bool(r[0]): (r[1], r[2]) for r in nw}
        line += "; " + ", ".join(f"{lab} {split[k][0]} avg {split[k][1]:+.2%}" for k, lab in
                                 ((True, "news"), (False, "no-news")) if k in split)
    return [line] + warn
