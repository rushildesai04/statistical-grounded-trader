"""Feed shadow, Stage 0: the nightly comparison and a self-test (``sgt.fast.feed_shadow`` says what Stage 0 is).

    python scripts/feed_shadow.py compare [--session YYYY-MM-DD]     the job (cron: weekdays 21:40 ET)
    python scripts/feed_shadow.py selftest --symbol SPY --session YYYY-MM-DD

``compare`` reads the recorder's own database, asks the vendor for the FINISHED one-minute candles of the symbols the
book looked at (the free feed, and the full tape, which is free once 15 minutes old), classifies every watched minute
and appends one line to ``reports/feed_shadow/daily.jsonl``. It runs after the session, throttled, on the existing
keys. It reads the operations database read-only (the session's longs, for the stop-detection rows) and writes
nothing there. No trade result is read or written: this measures the feed, not a strategy.

``selftest`` validates the whole path on delayed data with no live look: it replays a past session's free-feed
candles through a real recorder as if the book had looked every 15 s and each candle had arrived 3 s after its
minute, then runs the same comparison. Every traded minute must come out "delivered" within 15 s.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sgt.fast import feed_shadow as fs  # noqa: E402

OUT = Path("reports/feed_shadow/daily.jsonl")
OPS = Path("reports/ops/ops.db")
CHUNK, PAUSE_S, MAX_SYMBOLS, LIST_MAX = 20, 0.6, 200, 40


def last_session(now: datetime) -> str:
    from sgt.intraday.calendar import default_calendar

    cal, d = default_calendar(), now.astimezone(fs.ET).date()
    if not (cal.is_session(d) and now >= cal.bounds(d).rth_close + timedelta(minutes=20)):
        d -= timedelta(days=1)
        while not cal.is_session(d):
            d -= timedelta(days=1)
    return d.isoformat()


def recorded(db: Path, session: str) -> tuple[dict, dict, dict]:
    """(symbol -> look times, symbol -> {candle start: row}, the last health row) from the recorder's database."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    looks: dict[str, list] = {}
    for r in c.execute("SELECT symbol, at FROM looks WHERE session=? ORDER BY at", (session,)):
        looks.setdefault(r["symbol"], []).append(pd.Timestamp(r["at"]))
    live: dict[str, dict] = {}
    for r in c.execute("SELECT * FROM bars WHERE session=?", (session,)):
        live.setdefault(r["symbol"], {})[pd.Timestamp(r["ts"])] = {
            "closed_at": r["closed_at"], "closed_prev_look": r["closed_prev_look"],
            "values": (r["o"], r["h"], r["l"], r["c"], r["v"]), "revisions": r["revisions"]}
    h = c.execute("SELECT * FROM health ORDER BY at DESC LIMIT 1").fetchone()
    ms = [r[0] for r in c.execute("SELECT ms FROM looks WHERE session=? AND ms IS NOT NULL ORDER BY ms", (session,))]
    c.close()
    health = dict(h) if h else {}
    if ms:
        health["request_ms"] = {"median": ms[len(ms) // 2], "p95": ms[min(len(ms) - 1, int(0.95 * len(ms)))], "max": ms[-1]}
    return looks, live, health


def finished(feed: str, symbols: list[str], start: datetime, end: datetime, sleep=time.sleep) -> tuple[dict, list[str]]:
    """symbol -> {candle start: (o, h, l, c, v)} of the vendor's finished record, and the symbols that failed."""
    from sgt.intraday.sources import AlpacaHistoricalBars

    src, out, failed = AlpacaHistoricalBars(feed=feed), {}, []
    try:
        for i in range(0, len(symbols), CHUNK):
            part = symbols[i:i + CHUNK]
            try:
                got = src.fetch_many(part, start, end)
            except Exception as exc:  # noqa: BLE001 - external call: the symbols are reported as failed, the job goes on
                print(f"feed shadow: {feed} request failed for {len(part)} symbols ({type(exc).__name__})")
                failed += part
                continue
            for sym, bars in got.items():
                out[sym] = {pd.Timestamp(b.ts): (b.open, b.high, b.low, b.close, b.volume) for b in bars}
            sleep(PAUSE_S)
    finally:
        src.close()
    return out, failed


def longs_of(ops: Path, session: str) -> list[dict]:
    if not ops.exists():
        return []
    c = sqlite3.connect(f"file:{ops}?mode=ro", uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(
            "SELECT id, symbol, entry_ts, stop FROM long_trades WHERE session=? AND status IN ('open','est_closed','settled') "
            "AND entry_ts IS NOT NULL AND stop IS NOT NULL ORDER BY id", (session,))]
    finally:
        c.close()


def compare(session: str, db: Path = fs.DB, ops: Path = OPS, fetch=finished) -> dict:
    looks, live, health = recorded(db, session)
    first, close = fs.session_minutes(session)
    symbols = sorted(looks)[:MAX_SYMBOLS]
    out = {"session": session, "version": fs.VERSION, "made_at": datetime.now(timezone.utc).isoformat(),
           "symbols": len(symbols), "symbols_not_compared": max(0, len(looks) - MAX_SYMBOLS),
           "looks": sum(len(v) for v in looks.values()), "health": health}
    if not symbols:
        return {**out, "note": "no look was recorded for this session"}
    free, bad_free = fetch("iex", symbols, first.to_pydatetime(), close.to_pydatetime())
    tape, bad_tape = fetch("sip", symbols, first.to_pydatetime(), close.to_pydatetime())
    failed = sorted(set(bad_free) | set(bad_tape))
    rows = []
    for sym in symbols:
        if sym in failed:
            continue
        for r in fs.classify(looks[sym], live.get(sym, {}), free.get(sym, {}), tape.get(sym, {}), first, close):
            rows.append({**r, "symbol": sym})
    out.update(fs.summarize(rows))
    out["symbols_failed"] = failed
    flagged = [r for r in rows if r["cls"] in ("never_held", "live_only") or r.get("free_not_on_tape") or r.get("outside_tape")]
    out["listed"] = [{"symbol": r["symbol"], "minute": r["ts"].tz_convert(fs.ET).strftime("%H:%M"), "cls": r["cls"],
                      **{k: True for k in ("free_not_on_tape", "outside_tape", "changed") if r.get(k)}}
                     for r in flagged[:LIST_MAX]]
    out["listed_more"] = max(0, len(flagged) - LIST_MAX)
    stops = []
    for t in longs_of(ops, session):
        if t["symbol"] in failed or t["symbol"] not in tape:
            continue
        try:
            entry = pd.Timestamp(t["entry_ts"])
            got = fs.stop_seen(entry, float(t["stop"]), "long", live.get(t["symbol"], {}), tape[t["symbol"]], close)
        except (TypeError, ValueError):
            continue
        if got["tape_minute"] is not None or got["live_minute"] is not None:
            stops.append({"long_id": t["id"], "symbol": t["symbol"], **got})
    out["long_stops"] = stops
    return out


def write(line: dict, path: Path = OUT) -> None:
    """One line per session; a second run for the same session replaces its line."""
    path.parent.mkdir(parents=True, exist_ok=True)
    keep = []
    if path.exists():
        for ln in path.read_text().splitlines():
            try:
                if json.loads(ln).get("session") != line["session"]:
                    keep.append(ln)
            except ValueError:
                keep.append(ln)
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(keep + [json.dumps(line, sort_keys=True)]) + "\n")
    tmp.replace(path)


def words(o: dict) -> str:
    if o.get("note"):
        return f"feed shadow {o['session']}: {o['note']}"
    w = o["no_live_candle_was"]
    lag = o["lag_hi_s"]
    return (f"feed shadow {o['session']} ({o['version']}): {o['symbols']} symbols, {o['looks']} looks, "
            f"{o['watched_minutes']} watched minutes; no live candle in {o['no_live_candle']} "
            f"(no trade on the tape {w['no_trade_on_tape']}, tape only {w['tape_only']}, never held {w['never_held']}); "
            f"delivered {o['delivered']} (held by: median {lag['median']} s, p95 {lag['p95']} s, max {lag['max']} s); "
            f"free feed had a candle in {o['free_feed_share_of_tape_minutes']} of tape-traded minutes; "
            f"changed {o['changed']}, free not on tape {o['free_not_on_tape']}, outside tape {o['outside_tape']}; "
            f"failed symbols {len(o['symbols_failed'])}; recorder dropped {o['health'].get('dropped')}, "
            f"write fails {o['health'].get('fails')}")


def selftest(symbol: str, session: str) -> int:
    first, close = fs.session_minutes(session)
    free, bad = finished("iex", [symbol], first.to_pydatetime(), close.to_pydatetime())
    if bad or not free.get(symbol):
        print("selftest: no finished free-feed candles for", symbol, session)
        return 1
    bars = free[symbol]
    with tempfile.TemporaryDirectory() as d:
        db = Path(d) / "shadow.db"
        clock = {"at": first.to_pydatetime()}
        rec = fs.Recorder(db, clock=lambda: clock["at"]).start()
        at = first + timedelta(seconds=10)
        while at <= close + timedelta(minutes=3):
            have = sorted(m for m in bars if m + fs.BAR + timedelta(seconds=3) <= at)
            f = pd.DataFrame([(m, *bars[m]) for m in have], columns=["ts", "open", "high", "low", "close", "volume"])
            clock["at"] = at.to_pydatetime()
            rec.offer(symbol, at.to_pydatetime(), f, 1.0)
            while rec.q.qsize():                       # the clock is simulated: let the writer take each look in turn
                time.sleep(0.001)
            at += timedelta(seconds=15)
        rec.stop()
        o = compare(session, db, Path(d) / "none.db", fetch=lambda feed, syms, a, b: (
            ({symbol: bars}, []) if feed == "iex" else finished("sip", syms, a, b)))
    print(words(o))
    ok = (o["never_held"] == 0 and o["live_only"] == 0 and o["changed"] == 0 and o["delivered"] > 0
          and (o["lag_hi_s"]["max"] or 99) <= 18 and o["health"].get("fails") == 0)
    print("selftest:", "PASS" if ok else "FAIL", json.dumps({k: o[k] for k in ("delivered", "tape_only", "no_trade_on_tape",
                                                                             "never_held", "free_not_on_tape", "outside_tape")}))
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("compare")
    a.add_argument("--session")
    b = sub.add_parser("selftest")
    b.add_argument("--symbol", default="SPY")
    b.add_argument("--session", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "selftest":
        date.fromisoformat(args.session)
        return selftest(args.symbol.upper(), args.session)
    session = args.session or last_session(datetime.now(timezone.utc))
    date.fromisoformat(session)
    if not fs.DB.exists():
        print(f"feed shadow {session}: no recorder database (the recorder is off or has not run)")
        return 0
    o = compare(session)
    write(o)
    print(words(o))
    return 1 if o.get("symbols") and len(o.get("symbols_failed", [])) == o["symbols"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
