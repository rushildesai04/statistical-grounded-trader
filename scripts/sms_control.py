"""SMS control plane for the Fly runner (2026-11-02): see and approve every trade by text.

Usage (cron on Fly; see deploy/fly/crontab):
    python scripts/sms_control.py poll       # every minute: expire, read owner texts, answer, run approved orders
    python scripts/sms_control.py digest     # after the close: settle the approved book, text the daily digest
    python scripts/sms_control.py megacap    # propose the latest megacap decision to the approved book
    python scripts/sms_control.py tom        # turn-of-month entry/exit sessions -> approved book (SPY)
    python scripts/sms_control.py etf        # new monthly ETF sleeve snapshots -> approved book

The pre-registered research records are never changed here; the approved book is a separate
ledger (src/sgt/ops/book.py). Broker orders run only after OK <code> (src/sgt/ops/approvals.py).
Alpaca PAPER only.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from sgt.intraday.calendar import default_calendar
from sgt.ops import approvals, book, digest
from sgt.ops.sms import Sms
from sgt.ops.store import OpsStore, iso

ET = ZoneInfo("America/New_York")
SCRIPTS = Path(__file__).resolve().parent
LOOKBACK = timedelta(days=2)
FILL_DEADLINE = (15, 50)          # approvals must land before this ET time on the fill session
MEGACAP_SIZING = dict(risk_per_trade=0.01, stop_atr_mult=1.0, max_position_pct=0.25, gross_cap=1.0, beta_cap=1.0)
TOM_FIRST_ENTRY = date(2026, 11, 27)
log = logging.getLogger("sms_control")


# ------------------------------------------------------------------ data helpers
def next_session(d: date) -> date:
    cal, x = default_calendar(), d + timedelta(days=1)
    while not cal.is_session(x):
        x += timedelta(days=1)
    return x


def deadline(session: date) -> datetime:
    return datetime(session.year, session.month, session.day, *FILL_DEADLINE, tzinfo=ET)


def fill_session_for(now_et: datetime) -> date:
    """Today if it is a session and the deadline has not passed, else the next session."""
    today = now_et.date()
    if default_calendar().is_session(today) and now_et < deadline(today) - timedelta(minutes=30):
        return today
    return next_session(today)


def daily_closes(symbols: list[str], start: date, end: date) -> pd.DataFrame:
    from sgt.megacap.live import alpaca_bars, to_daily

    end_ts = min(datetime.combine(end + timedelta(days=1), datetime.min.time(), ET), datetime.now(ET) - timedelta(minutes=16))
    frames = alpaca_bars(symbols, "1Day", start.isoformat(), end_ts.isoformat(), feed="sip", adjustment="all")
    return pd.DataFrame({s: to_daily(f).close for s, f in frames.items()}).sort_index()


def daily_bars(symbols: list[str], days: int = 400) -> dict[str, pd.DataFrame]:
    from sgt.megacap.live import alpaca_bars, to_daily

    end = datetime.now(ET) - timedelta(minutes=16)
    frames = alpaca_bars(symbols, "1Day", (end - timedelta(days=days)).date().isoformat(), end.isoformat(),
                         feed="sip", adjustment="all")
    return {s: to_daily(f) for s, f in frames.items()}


def paper_get(path: str) -> httpx.Response:
    from sgt.config import get_settings
    from sgt.intraday.sources import _alpaca_headers

    s = get_settings()
    r = httpx.get(s.alpaca_paper_url.rstrip("/") + path, headers=_alpaca_headers(), timeout=s.http_timeout_s)
    r.raise_for_status()
    return r


def broker_line() -> str:
    try:
        a = paper_get("/v2/account").json()
        eq, last = float(a["equity"]), float(a["last_equity"])
        pos = paper_get("/v2/positions").json()
        held = ", ".join(f"{p['symbol']} {p['qty']}" for p in pos[:6]) or "no positions"
        return f"Alpaca paper: ${eq:,.0f} ({eq / last - 1:+.2%} today) {held}"
    except Exception:  # noqa: BLE001 - digest still goes out; the gap is shown, detail in the log
        log.exception("broker line failed")
        return "Alpaca paper: could not reach Alpaca tonight (shown again tomorrow)"


# ------------------------------------------------------------------ broker executors
def run_rebalance(row) -> tuple[bool, str]:
    cmd = [sys.executable, str(SCRIPTS / "rebalance_passive.py"), "--execute", "--approval-id", str(row["id"]),
           "--max-order-notional", os.environ.get("SGT_MAX_ORDER_NOTIONAL", "75000")]
    with open("reports/rebalance/run.log", "a") as fh:
        fh.write(f"=== {datetime.now(ET):%Y-%m-%d %H:%M:%S %Z} approved #{row['id']}: {' '.join(cmd[1:])}\n")
        fh.flush()
        r = subprocess.run(cmd, stdout=fh, stderr=subprocess.STDOUT, timeout=900)
        if r.returncode:
            fh.write(f"!!! exit {r.returncode}\n")
    return r.returncode == 0, f"rebalance exit {r.returncode}"


EXECUTORS = {"passive_60_40": run_rebalance}


def flatten(db: OpsStore, send) -> tuple[bool, str]:
    """Pause FIRST, then close every Alpaca PAPER position and zero every approved-book strategy at the
    next close. The broker result is verified by re-reading positions; any doubt is reported as failure."""
    from sgt.config import get_settings
    from sgt.intraday.sources import _alpaca_headers

    approvals.pause(db, reason="flatten_pause")
    s = get_settings()
    url = s.alpaca_paper_url.rstrip("/") + "/v2/positions"
    ok = True
    try:
        r = httpx.delete(url, params={"cancel_orders": "true"}, headers=_alpaca_headers(), timeout=s.http_timeout_s)
        bad = [x.get("symbol") for x in (r.json() if r.status_code == 207 else []) if isinstance(x, dict)
               and int(x.get("status", 200)) >= 400]
        left = httpx.get(url, headers=_alpaca_headers(), timeout=s.http_timeout_s).json()
        open_syms = [x.get("symbol") for x in left if isinstance(x, dict)] if isinstance(left, list) else ["?"]
        ok = r.status_code in (200, 207) and not bad
        broker = (f"broker close-all HTTP {r.status_code}" + (f", failed {bad}" if bad else "")
                  + (f"; still open (closing orders may be queued until the open): {open_syms}" if open_syms else ""))
    except Exception:  # noqa: BLE001 - reported to the owner in plain words; trading already paused
        log.exception("broker close-all failed")
        ok, broker = False, "Alpaca did not confirm closing everything; positions may still be open (check the Alpaca app)"
    fill = fill_session_for(datetime.now(ET))
    with db.tx() as c:
        strategies = [x[0] for x in c.execute("SELECT DISTINCT strategy FROM book_orders")]
    for strat in strategies:
        cur = book.projected(db, strat)
        if cur:
            book.propose(db, send, strategy=strat, targets={k: 0.0 for k in cur}, fill_session=fill,
                         deadline=deadline(fill), label=f"{strat} FLATTEN")
    db.audit("flatten", broker=broker, ok=ok)
    return ok, (f"FLATTEN {'done' if ok else 'INCOMPLETE'}: {broker}; book zeroed at {fill:%m-%d} close; "
                "trading PAUSED (RESUME to restart).")


# ------------------------------------------------------------------ commands
def status_text(db: OpsStore) -> str:
    pend = approvals.pending(db)
    lines = ["[SGT] status" + ("  ** PAUSED **" if db.paused() else "")]
    lines += digest.book_lines(db, datetime.now(ET).date()) or ["book: flat"]
    lines.append(broker_line())
    lines.append(f"pending: {len(pend)}" + "".join(f"\n {p['strategy']}: OK {p['code']}" for p in pend))
    return "\n".join(lines)


def handle(db: OpsStore, sms: Sms, cmd: approvals.Command | None, sid: str) -> str:
    if cmd is None:
        return "Not understood. " + approvals.HELP
    if cmd.verb == "CMDS":
        return approvals.HELP
    if cmd.verb == "STATUS":
        return status_text(db)
    if cmd.verb == "PENDING":
        p = approvals.pending(db)
        return "\n".join(f"{r['summary'][:140]} -> OK {r['code']}" for r in p) or "Nothing pending."
    if cmd.verb == "PAUSE":
        voided = approvals.pause(db)
        return ("PAUSED: pending requests expired" + (f", {voided} approved entries cancelled" if voided else "")
                + ". Exits still apply. RESUME to restart.")
    if cmd.verb == "DISARM":
        approvals.disarm(db)
        return ("DISARMED: no new fast trades today, including auto-entries at expiry. Open fast trades still "
                "exit on their rules.")
    if cmd.verb in ("RESUME", "FLATTEN", "ARM") and cmd.code is None:
        if approvals.challenge(db, sms.send, cmd.verb.lower()) is None:
            return f"A {cmd.verb} code was already sent and is still valid (10 min); reply {cmd.verb} <code>."
        return ""                                  # the challenge text is the reply
    reply, row = approvals.answer(db, cmd, sid)
    if row is None or row["status"] != "approved":
        return reply
    if row["kind"] == "control":
        action = json.loads(row["payload"])["action"]
        if not approvals.claim(db, row["id"]):
            return "That code expired before it could be used; send the command again."
        if action == "arm":
            if db.paused():
                approvals.finish(db, row["id"], False, "paused")
                return "Trading is PAUSED: RESUME first, then ARM."
            day = approvals.arm(db)
            approvals.finish(db, row["id"], True, f"armed {day}")
            return f"ARMED for {day}: fast alert-fade trades enter automatically (capped); each one is texted. DISARM or PAUSE stops new ones."
        if action == "resume":
            db.set_flag("paused", "0")
            db.audit("resume", sid=sid)
            approvals.finish(db, row["id"], True, "resumed")
            return "RESUMED: trading enabled."
        try:
            ok, out = flatten(db, sms.send)
        except Exception:  # noqa: BLE001 - always finish the row and tell the owner (plain words; detail logged)
            log.exception("flatten failed")
            ok, out = False, "FLATTEN could not finish; trading is PAUSED. Check the Alpaca app for open positions."
        approvals.finish(db, row["id"], ok, out)
        return out
    if row["kind"] == "book":
        return f"{reply}\n{book.apply_approved(db, row)}"
    if row["kind"] == "fade":                      # the fade listener claims it within ~10 s and texts the entry
        return f"{reply}\nEntering at the next quote; the entry text follows."
    return f"{reply}\nExecuting now; fills follow."


def dispatch_broker(db: OpsStore, sms: Sms) -> None:
    with db.tx() as c:
        rows = c.execute("SELECT * FROM approvals WHERE status='approved' AND kind='broker' AND expires_at>?",
                         (iso(datetime.now(timezone.utc)),)).fetchall()
    for row in rows:
        fn = EXECUTORS.get(row["strategy"])
        if fn is None:
            approvals.claim(db, row["id"])
            approvals.finish(db, row["id"], False, "no executor")
            continue
        try:
            ok, msg = fn(row)                  # the executor claims/finishes the approval itself
        except subprocess.TimeoutExpired:
            ok, msg = False, "timeout"
        if approvals.claim(db, row["id"]):    # executor exited without claiming: never retry it
            approvals.finish(db, row["id"], False, f"not executed ({msg}): outside the window, no orders, "
                                                   "or already done this month")
            ok, msg = False, "not executed (outside window / nothing to trade)"
        if not ok:
            sms.send(f"[SGT] {row['strategy']} approval #{row['id']} did not execute: {msg}. Nothing else changed.")


REPLY_CAP_PER_HOUR = 20
UNPARSED_EVERY = timedelta(minutes=30)


def send_capped(db: OpsStore, sms: Sms, text: str, unparsed: bool = False) -> None:
    """Replies are capped (an auto-responder or a spoofed flood must not become an SMS storm)."""
    now = datetime.now(timezone.utc)
    if db.count_events("reply", now - timedelta(hours=1)) >= REPLY_CAP_PER_HOUR or \
            (unparsed and db.count_events("reply_unparsed", now - UNPARSED_EVERY)):
        db.audit("reply_suppressed", unparsed=unparsed)
        return
    sms.send(text)
    db.audit("reply_unparsed" if unparsed else "reply")
    if unparsed:
        db.audit("reply")


def cmd_poll(db: OpsStore) -> int:
    now = datetime.now(timezone.utc)
    if db.flag("control_since") is None:            # ignore every text sent before SMS control existed
        db.set_flag("control_since", now.isoformat())
    since = datetime.fromisoformat(db.flag("control_since"))
    approvals.expire(db, now)
    sms = Sms()
    try:
        try:
            for m in sms.inbound(max(since, now - LOOKBACK)):
                with db.tx() as c:
                    new = c.execute("INSERT OR IGNORE INTO inbound(sid, sent_at, body, handled_at) VALUES (?,?,?,?)",
                                    (m.sid, m.sent_at.isoformat(), m.body[:80], now.isoformat())).rowcount
                if not new or m.sent_at < since:
                    continue
                cmd = approvals.parse(m.body)
                reply = handle(db, sms, cmd, m.sid)
                with db.tx() as c:
                    c.execute("UPDATE inbound SET outcome=? WHERE sid=?", ((cmd.verb if cmd else "unparsed"), m.sid))
                if reply:
                    send_capped(db, sms, reply, unparsed=cmd is None)
        finally:                                    # dispatch + sweep even when Twilio listing fails
            dispatch_broker(db, sms)
            for r in approvals.sweep(db):
                sms.send(f"[SGT] {r['strategy']} approval #{r['id']} was {r['status']} but did not complete; "
                         "marked FAILED. Check STATUS / the broker.")
    finally:
        sms.close()
    return 0


def last_final_session(now_et: datetime) -> date:
    cal, d = default_calendar(), now_et.date()
    if cal.is_session(d) and now_et >= cal.bounds(d).rth_close.astimezone(ET) + timedelta(minutes=20):
        return d
    d -= timedelta(days=1)
    while not cal.is_session(d):
        d -= timedelta(days=1)
    return d


def cmd_digest(db: OpsStore) -> int:
    now = datetime.now(ET)
    fills, problems = book.settle(db, daily_closes, last_final_session(now))
    text = digest.build(db, now, fills=fills, problems=problems, broker=broker_line(),
                        research=[digest.megacap_research(), digest.challenger_research()])
    sms = Sms()
    try:
        sms.send(text)
    finally:
        sms.close()
    print(text)
    return 0


def megacap_targets(positions: dict[str, int]) -> dict[str, float]:
    from sgt.risk.sizing import SizingConfig, asset_risk, size_book

    bars = daily_bars(list(positions) + ["SPY"])
    risk = {s: asset_risk(s, bars[s], bars["SPY"].close) for s in positions if s in bars}
    sized = size_book({s: float(p) for s, p in positions.items()}, risk, 1.0, SizingConfig(**MEGACAP_SIZING))
    for s, why in sized.rejected.items():
        log.warning("megacap sizing rejected %s: %s", s, why)
    return {s: round(sized.weights.get(s, 0.0), 4) for s in positions}


def done(db: OpsStore, key: str) -> bool:
    return db.flag(f"done:{key}") is not None


def mark_done(db: OpsStore, key: str) -> None:
    """Set only after a proposal succeeded, so a failed run is retried by the next cron pass."""
    db.set_flag(f"done:{key}", "1")


def cmd_megacap(db: OpsStore) -> int:
    files = sorted((Path("reports/megacap_forward/decisions")).glob("*.json"))
    if not files:
        print("no megacap decisions")
        return 0
    dec = json.loads(files[-1].read_text())
    d = date.fromisoformat(dec["date"])
    fill = next_session(d)
    if datetime.now(ET) >= deadline(fill) or done(db, f"megacap:{d}"):
        print(f"megacap {d}: already proposed or past the {fill} deadline")
        return 0
    targets = megacap_targets({s: int(v["position"]) for s, v in dec["positions"].items()})
    sms = Sms()
    try:
        res = book.propose(db, sms.send, strategy="megacap", targets=targets, fill_session=fill,
                           deadline=deadline(fill), label=f"megacap {d:%m-%d}")
        mark_done(db, f"megacap:{d}")
    finally:
        sms.close()
    print(f"megacap {d}: {res}")
    return 0


def tom_role(d: date) -> str | None:
    """'entry' on the 2nd-to-last session of a month, 'exit' on the 3rd session (anomalies A3 windows)."""
    cal = default_calendar()
    if not cal.is_session(d):
        return None
    month = [date(d.year, d.month, k) for k in range(1, 32) if _valid(d.year, d.month, k)]
    sessions = [x for x in month if cal.is_session(x)]
    if len(sessions) >= 2 and d == sessions[-2]:
        return "entry"
    if len(sessions) >= 3 and d == sessions[2]:
        return "exit"
    return None


def _valid(y: int, m: int, k: int) -> bool:
    try:
        date(y, m, k)
        return True
    except ValueError:
        return False


def cmd_tom(db: OpsStore) -> int:
    now = datetime.now(ET)
    d, role = now.date(), tom_role(now.date())
    if role is None or d < TOM_FIRST_ENTRY or now >= deadline(d) or done(db, f"tom:{d}"):
        print(f"tom {d}: nothing to do ({role})")
        return 0
    sms = Sms()
    try:
        res = book.propose(db, sms.send, strategy="tom", targets={"SPY": 1.0 if role == "entry" else 0.0},
                           fill_session=d, deadline=deadline(d), label=f"turn-of-month {role}")
        mark_done(db, f"tom:{d}")
    finally:
        sms.close()
    print(f"tom {d} {role}: {res}")
    return 0


def cmd_etf(db: OpsStore) -> int:
    now = datetime.now(ET)
    sms = Sms()
    try:
        for p in sorted(Path("reports/forward").glob("20??-??.json")):
            fill = fill_session_for(now)
            if done(db, f"etf:{p.stem}"):
                continue
            weights = json.loads(p.read_text())["weights"]
            for sleeve, w in weights.items():
                book.propose(db, sms.send, strategy=f"etf:{sleeve}", targets={k: float(v) for k, v in w.items()},
                             fill_session=fill, deadline=deadline(fill), label=f"ETF {sleeve} {p.stem}")
            mark_done(db, f"etf:{p.stem}")
            print(f"etf {p.stem}: proposed {list(weights)} for {fill}")
    finally:
        sms.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["poll", "digest", "megacap", "tom", "etf"])
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    db = OpsStore()
    return {"poll": cmd_poll, "digest": cmd_digest, "megacap": cmd_megacap, "tom": cmd_tom, "etf": cmd_etf}[args.cmd](db)


if __name__ == "__main__":
    raise SystemExit(main())
