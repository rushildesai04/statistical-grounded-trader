"""Live paper LONG book (owner 2026-11-10, option A "longs first"): the trader's long setups traded on paper as they
happen, texted, listed before shorts. Paper only, no orders.

How it trades: every minute, for each research alert of the last WATCH + a few minutes (including alerts the SHORT
skipped for news or beta >= 3), the tested detectors and fill simulator (`sgt.fast.patterns`, the code of
`prereg_long_setups_2026-11-10`) run on today's CLOSED 1-minute bars. The simulator never looks ahead, so the live entry
and exit are what the test computes on the same bars. One long per alert: the first long setup to fill (ties: bull flag,
red-candle break, break of structure, red-candle close). A halt after the alert (no bar within 5 minutes) uses the
reopen versions (L7). Live bars are IEX (SIP is 15 minutes late on this plan); the OFFICIAL result is the long shadow
row of the same alert and setup (SIP, nightly), exactly as the fade's estimate vs settle.

Honest label: every long setup LOST on past alerts (-1.2% to -2.8% per trade); this book exists to show live whether
that holds. Respects PAUSE / DISARM; no entry after the last entry minute; at most MAX_OPEN open longs.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Callable

import pandas as pd

from sgt.fast import fade, long_shadow
from sgt.fast import patterns as pt
from sgt.fast.fade import ET
from sgt.ops import approvals
from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
LONG_KINDS = ("L5v2", "L5", "L4b", "L6", "L4a", "L7b", "L7a")     # tie order when two fill on the same bar
NAMES = {"L5v2": "bull flag breakout", "L5": "bull flag breakout (v1)", "L4b": "red-candle break",
         "L6": "break of structure",
         "L4a": "red-candle close", "L7b": "halt reopen: red-candle break", "L7a": "halt reopen: red-candle close"}
TESTED_N = {"L4a": 2072, "L4b": 754, "L5": 247, "L6": 1149, "L7a": 77, "L7b": 31,     # prereg_long_setups training
            "L5v2": 199}                                                                   # prereg_flag_v2 training
MAX_OPEN = 40
GRACE = pd.Timedelta(minutes=pt.TRIGGER_BARS + 2)          # after WATCH: the last setup's order can still fill
MARGIN = pd.Timedelta(minutes=15)                           # candidates outlive the window so the "none" marker lands
# 2026-11-10: after the 11:45 deploy the book booked KULR's 10:39 fill and texted entry + exit at once. A fill whose
# bar started longer ago than this was not seen live (restart, outage): recorded as 'missed', never traded or texted.
# Normal detection is the fill bar's close + one 30 s pass, well inside it. The official record is the shadow anyway.
LIVE_LAG = pd.Timedelta(minutes=5)
LiveBars = Callable[[str, datetime], pd.DataFrame]          # (symbol, now) -> today's 1-minute bars (ts = start, UTC)


def rth(f: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """Today's regular-session bars that have CLOSED by ``now`` (the research uses RTH bars only)."""
    if f is None or f.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    et = f.ts.dt.tz_convert(ET)
    m = et.dt.hour * 60 + et.dt.minute
    keep = (m >= 9 * 60 + 30) & (m < 16 * 60) & (f.ts + pt.BAR <= pd.Timestamp(now))
    return f[keep].sort_values("ts").reset_index(drop=True)


def candidates(db: OpsStore, now: datetime) -> list[dict]:
    """Today's research alerts still inside their long window, with no long trade yet."""
    session = now.astimezone(ET).date().isoformat()
    since = iso(now - (pt.WATCH + GRACE + MARGIN + pd.Timedelta(fade.MAX_ENTRY_DELAY)
                       + long_shadow.REOPEN_MAX).to_pytimedelta())
    with db.tx() as c:
        rows = c.execute(
            "SELECT id, symbol, session, posted_at, status, reason FROM fade_trades f WHERE session=? AND posted_at>=? "
            "AND " + long_shadow.QUALIFY + " AND " + long_shadow.FIRST + " AND NOT EXISTS (SELECT 1 FROM long_trades t "
            "WHERE t.fade_id=f.id) ORDER BY id",
            (session, since, *long_shadow.QUALIFY_ARGS, *long_shadow.QUALIFY_ARGS)).fetchall()
    return [dict(r) for r in rows]


def first_fill(f: pd.DataFrame, row: dict, latest: pd.Timestamp) -> tuple[pt.Setup, pt.Trade] | None:
    """The first long setup to fill on these bars (None = none yet)."""
    found, _ = long_shadow.setups(f, {**row, "status": "live"})     # halts decided from the bars, as the shadow
    fills = []
    for s in found:
        if s.side != "long":
            continue
        t = pt.simulate(f, s, latest=latest)
        if t is not None:
            fills.append((t.entry_ts, LONG_KINDS.index(s.kind), s, t))
    if not fills:
        return None
    _, _, s, t = min(fills, key=lambda x: (x[0], x[1]))
    return s, t


def _window_over(row: dict, now: datetime) -> bool:
    posted = pd.Timestamp(row["posted_at"]).tz_convert("UTC")
    return pd.Timestamp(now) >= posted + pd.Timedelta(fade.MAX_ENTRY_DELAY) + long_shadow.REOPEN_MAX + pt.WATCH + GRACE


def tick(db: OpsStore, bars: LiveBars, send, now: datetime | None = None) -> int:
    """One pass: open new longs, then advance open ones. Returns the number of longs opened."""
    now = now or utcnow()
    opened = 0
    for row in candidates(db, now):
        try:
            if db.paused() or approvals.disarmed(db, now):
                continue
            with db.tx() as c:
                n_open = c.execute("SELECT COUNT(*) FROM long_trades WHERE status='open'").fetchone()[0]
            if n_open >= MAX_OPEN:
                continue
            f = rth(bars(row["symbol"], now), now)
            hit = first_fill(f, row, long_shadow._latest(row["session"])) if len(f) else None
            if hit is None:
                if _window_over(row, now):
                    _insert(db, row, None, None, "none", now)
                continue
            s, t = hit
            if pd.Timestamp(now) - t.entry_ts > LIVE_LAG:
                _insert(db, row, s, t, "missed", now)
                log.info("long %s %s filled %s, before the book saw it: missed", row["symbol"], s.kind, t.entry_ts)
                continue
            if _insert(db, row, s, t, "open", now):
                opened += 1
                send(entry_text(db, row["symbol"], s, t))
        except Exception:  # noqa: BLE001 - one alert failing never stops the book; logged with its id
            log.exception("long candidate %s failed", row["id"])
    _advance(db, bars, send, now)
    return opened


def _insert(db: OpsStore, row: dict, s: pt.Setup | None, t: pt.Trade | None, status: str, now: datetime) -> bool:
    v = nav(db)
    w = fade.trade_weight(db, v) if t is not None else None     # the SIZE setting against the LONG book's value
    with db.tx() as c:
        return c.execute(
            "INSERT OR IGNORE INTO long_trades(fade_id, symbol, session, kind, status, trigger, stop, target, weight, "
            "entry_ts, est_entry, est_last, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["symbol"], row["session"], s.kind if s else None, status, s.trigger if s else None,
             s.stop if s else None, target(s, t) if s else None, w, iso(t.entry_ts.to_pydatetime()) if t else None,
             t.entry if t else None, t.entry if t else None, iso(now))).rowcount == 1


def target(s: pt.Setup, t: pt.Trade) -> float:
    return t.entry + pt.RR * (t.entry - s.stop)


def _advance(db: OpsStore, bars: LiveBars, send, now: datetime) -> None:
    """Close open longs when the simulator's exit is final on closed bars: a stop or target, or a time exit once the
    whole hold window (or the session) has closed."""
    with db.tx() as c:
        rows = [dict(r) for r in c.execute("SELECT t.*, f.posted_at, f.status AS fstatus FROM long_trades t JOIN "
                                           "fade_trades f ON f.id=t.fade_id WHERE t.status='open' ORDER BY t.id")]
    for r in rows:
        try:
            f = rth(bars(r["symbol"], now), now)
            if f.empty:
                continue
            entry_ts = pd.Timestamp(r["entry_ts"])
            t = _exit(f, r, entry_ts)
            if t is None:
                continue
            close = pd.Timestamp(default_close(r["session"]))
            final = t.reason != "time" or pd.Timestamp(now) >= min(entry_ts + pt.HOLD, close)
            if not final:
                _set(db, r["id"], "open", est_last=float(f.close.iloc[-1]))
                continue
            if _set(db, r["id"], "open", status="est_closed", est_exit=t.exit, est_exit_at=iso(t.exit_ts.to_pydatetime()),
                    est_last=t.exit, reason=t.reason):
                send(exit_text(db, r, t))
        except Exception:  # noqa: BLE001 - one trade failing never stops the book; logged with its id
            log.exception("long trade %s failed", r["id"])


def _exit(f: pd.DataFrame, r: dict, entry_ts: pd.Timestamp) -> pt.Trade | None:
    """Exit of an open long on closed bars: stop first, then the 2R target (not on a stop-entry's fill bar), else a
    time exit at the last bar within HOLD (final only once the window has closed)."""
    entry, stop, tgt = float(r["est_entry"]), float(r["stop"]), float(r["target"])
    stop_entry = r["trigger"] is not None
    path = f[(f.ts >= entry_ts) & (f.ts < entry_ts + pt.HOLD)]
    for k, b in enumerate(path.itertuples()):
        first = k == 0
        if b.low <= stop:
            px = stop if first and stop_entry else min(stop, b.open)
            return pt.Trade(r["kind"], "long", entry_ts, entry, b.ts, px, "stop", px / entry - 1)
        if b.high >= tgt and not (first and stop_entry):
            px = max(tgt, b.open)
            return pt.Trade(r["kind"], "long", entry_ts, entry, b.ts, px, "target", px / entry - 1)
    if path.empty:
        return None
    last = path.iloc[-1]
    return pt.Trade(r["kind"], "long", entry_ts, entry, last.ts, float(last.close), "time", float(last.close) / entry - 1)


def default_close(session: str) -> datetime:
    from sgt.intraday.calendar import default_calendar
    return default_calendar().bounds(date.fromisoformat(session)).rth_close


def _set(db: OpsStore, rid: int, expect: str, **cols) -> bool:
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:
        return c.execute(f"UPDATE long_trades SET {keys} WHERE id=? AND status=?",  # noqa: S608 - fixed column names
                         (*cols.values(), rid, expect)).rowcount == 1


def nav(db: OpsStore, through: date | None = None) -> float:
    """Official long book value: each settled long's official (shadow) net at its own weight; ``through`` = only
    sessions up to that date (the value before a later day's trades, as fade.nav)."""
    with db.tx() as c:
        rows = c.execute("SELECT t.weight, s.net FROM long_trades t JOIN long_shadow s ON s.fade_id=t.fade_id AND "
                         "s.kind=t.kind WHERE t.status='settled' AND s.net IS NOT NULL"
                         + (" AND t.session<=?" if through else "") + " ORDER BY t.id",
                         ((through.isoformat(),) if through else ())).fetchall()
    v = fade.NAV0
    for r in rows:
        v *= 1 + r["weight"] * r["net"]
    return v


STALE_DAYS = 3                                     # a closed long with no official event after this is void


def settle(db: OpsStore, now: datetime | None = None) -> list[str]:
    """After the shadow run: book each closed live long at its official shadow net (same alert, same setup). Void,
    never left hanging (review F-1: a silent drop would bias the record, mostly losing L7 trades): a matching setup
    that did not fill on SIP; an event the shadow processed without that setup (e.g. IEX gaps looked like a halt
    live, SIP had none); a long whose alert never became an official event within STALE_DAYS."""
    now = now or utcnow()
    today = now.astimezone(ET).date().isoformat()
    with db.tx() as c:                    # final pass F-1: a long still open from an earlier session (listener down
        c.execute("UPDATE long_trades SET status='est_closed', reason='closed by settle (listener missed the exit)' "
                  "WHERE status='open' AND session<?", (today,))      # across the close) gets its official result
        rows = [dict(r) for r in c.execute(
            "SELECT t.id, t.symbol, t.kind, s.net, s.entry FROM long_trades t JOIN long_shadow s ON s.fade_id=t.fade_id "
            "AND s.kind=t.kind WHERE t.status='est_closed'")]
        orphans = [dict(r) for r in c.execute(
            "SELECT t.id, t.symbol, t.kind, t.session, EXISTS(SELECT 1 FROM long_shadow s WHERE s.fade_id=t.fade_id) "
            "AS seen FROM long_trades t WHERE t.status='est_closed' AND NOT EXISTS (SELECT 1 FROM long_shadow s WHERE "
            "s.fade_id=t.fade_id AND s.kind=t.kind)")]
    out = []
    stale = (now.astimezone(ET).date() - timedelta(days=STALE_DAYS)).isoformat()
    for r in orphans:
        why = ("official bars show a different setup (IEX vs SIP)" if r["seen"] else
               "no official event" if r["session"] <= stale else None)
        if why and _set(db, r["id"], "est_closed", status="void", reason=why):
            out.append(f"{r['symbol']} {r['kind']} void")
    for r in rows:
        if r["net"] is None:
            _set(db, r["id"], "est_closed", status="void", reason="setup did not fill on SIP bars")
            out.append(f"{r['symbol']} {r['kind']} void")
        elif _set(db, r["id"], "est_closed", status="settled", net=float(r["net"])):
            out.append(f"{r['symbol']} {r['kind']} {float(r['net']):+.2%}")
    return out


def entry_text(db: OpsStore, symbol: str, s: pt.Setup, t: pt.Trade) -> str:
    v = nav(db)
    usd = fade.trade_weight(db, v) * v
    risk = (t.entry - s.stop) / t.entry
    out = min(t.entry_ts + pt.HOLD, pd.Timestamp(default_close(t.entry_ts.tz_convert(ET).date().isoformat())))
    return (f"[SGT] 🟢 LONG {symbol} @ ${t.entry:.2f} · ${usd:,.0f} (paper)\n"
            f"Setup: {NAMES.get(s.kind, s.kind)} · stop ${s.stop:.2f} ({-risk:.1%}) · target ${target(s, t):.2f} "
            f"(+{pt.RR * risk:.1%}) · out by {out.tz_convert(ET):%H:%M} ET\n"
            f"Tested on past alerts: {long_shadow.TESTED.get(s.kind, 0):+.1%}/trade over {TESTED_N.get(s.kind, 0):,} "
            f"trades (live paper test)\nhttps://www.tradingview.com/chart/?symbol={symbol}")


def exit_text(db: OpsStore, r: dict, t: pt.Trade) -> str:
    usd = (r["weight"] or fade.WEIGHT) * nav(db) * t.gross           # final pass F-2: the long book's own value
    word = {"stop": "stopped", "target": "target hit", "time": "time exit"}[t.reason]
    return (f"[SGT] 🟢 LONG OUT {r['symbol']} · {'made' if t.gross > 0 else 'lost'} {t.gross:+.1%} "
            f"({'+' if usd >= 0 else '-'}${abs(usd):,.0f} est, paper) · {word} · ${t.entry:.2f} -> ${t.exit:.2f}")


def today_line(db: OpsStore, session: str) -> str | None:
    """Digest TODAY line for the long book (estimates until settled)."""
    with db.tx() as c:
        rows = c.execute("SELECT symbol, kind, status, est_entry, est_exit, net, weight FROM long_trades WHERE "
                         "session=? AND status NOT IN ('none','missed') ORDER BY id", (session,)).fetchall()
    # review F-2: a void long has no official result: say so instead of showing its estimate
    if not rows:
        return None
    bits = []
    for r in rows:
        g = r["net"] if r["net"] is not None else (r["est_exit"] / r["est_entry"] - 1 if r["est_exit"] else None)
        state = "void" if r["status"] == "void" else ("open" if g is None else f"{g:+.1%}")
        bits.append(f"{r['symbol']} {NAMES.get(r['kind'], r['kind'])} {state}")
    return f"longs (paper) {len(rows)} trades: " + ", ".join(bits)


def record_line(db: OpsStore) -> str | None:
    with db.tx() as c:
        n, m = c.execute("SELECT COUNT(*), AVG(net) FROM long_trades WHERE status='settled'").fetchone()
    if not n:
        return None
    return (f"long book (paper) {n} trades, avg {m:+.2%}/trade, book ${nav(db):,.0f}; "
            f"tested {min(long_shadow.TESTED.values()):+.1%} to {max(long_shadow.TESTED.values()):+.1%}")
