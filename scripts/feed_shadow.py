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

# The after-the-session check of docs/findings/proposal_realtime_feed_shadow_2026-11-27.md, section 4a, made by the
# job itself so that it does not depend on a person reading two files (owner 2026-11-27: "Collect the first full
# session's recorder measurements against the predefined tolerances"). FROZEN on 2026-11-27, before the recorder had
# run on the runner (owner: "Do not tighten or loosen thresholds after seeing the recorder's results to manufacture a
# pass"): these four numbers are that section's table and change only on the owner's word. The figures come from the
# long book's own cadence record (sgt.ops.own_thread.Pace); nothing is measured anew and nothing is switched by it.
PACE, LOOP = Path("reports/ops/loop_gaps.jsonl"), "long book"
TOL = {"within_min": 0.995, "late_max": 0, "work_p90_max_s": 0.2, "work_max_max_s": 0.5}
LIMITS = ("baseline of two full sessions; the record keeps one figure per 10 minutes (the longest pass) to 0.1 s, so "
          "a cost under 0.1 s a pass cannot be seen; a figure inside the tolerance is not proof of no effect")


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
    off = dict(c.execute("SELECT key, value FROM meta WHERE key IN ('off_at', 'off_why')").fetchall())
    ms = [r[0] for r in c.execute("SELECT ms FROM looks WHERE session=? AND ms IS NOT NULL ORDER BY ms", (session,))]
    c.close()
    health = dict(h) if h else {}
    marker = Path(db).parent / fs.OFF_NAME          # the off switch: by hand, or written by a safety trip
    if off or marker.exists():
        note = ""
        try:
            note = marker.read_text(encoding="utf-8")[:300].strip() if marker.exists() else ""
        except OSError:
            note = "its OFF file could not be read"
        health["off"] = {"at": off.get("off_at"), "why": off.get("off_why") or note or "the OFF file exists"}
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


def pace(session: str, path: Path = PACE) -> dict:
    """The long book's own cadence record over the session's regular hours, set against the frozen tolerance (TOL).
    The window is the baseline read's: rows stamped from 10 minutes after the open (a row is written at the END of
    its 10 minutes) to the minute of the close. Never raises: a record that cannot be read is said in ``note`` and
    every verdict stays None (not measured), never True."""
    first, close = fs.session_minutes(session)
    lo, hi = first + timedelta(minutes=10), close + timedelta(seconds=59)
    out = {"loop": LOOP, "window_et": f"{lo.tz_convert(fs.ET):%H:%M}-{close.tz_convert(fs.ET):%H:%M}",
           "full_session": close - first == timedelta(hours=6, minutes=30), "tolerance": TOL, "limits": LIMITS,
           "inside": {"cadence": None, "late": None, "work_p90": None, "work_max": None}}
    try:
        raw = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError as e:
        return {**out, "note": f"the cadence record could not be read ({type(e).__name__})"}
    n, counts, work, late, unread = 0, [0, 0, 0, 0], [], 0, 0
    for ln in raw:
        try:
            r = json.loads(ln)
            if r.get("loop") != LOOP:
                continue
            at = pd.Timestamp(r["at"])
            if at.tzinfo is None or not lo <= at <= hi:
                continue
            if r.get("kind") == "summary":
                c = [int(x) for x in r["counts"]]
                if len(c) != 4:
                    raise ValueError("counts")
                n, counts, work = n + int(r["n"]), [x + y for x, y in zip(counts, c)], work + [float(r["max_work"])]
            elif "gap" in r:
                late += 1                               # a late pass has a line of its own
        except (ValueError, TypeError, KeyError, AttributeError):
            unread += 1
    out.update(passes=n, counts=counts, late=late, periods=len(work), unreadable_lines=unread)
    if not n:
        return {**out, "note": "no cadence row of the long book in the session's regular hours"}
    w = sorted(work)
    share, p90, top = counts[0] / n, w[int(0.9 * (len(w) - 1))], w[-1]     # the 90th percentile as the baseline took it
    out.update(within=round(share, 5), work_p90_s=p90, work_max_s=top)
    out["inside"] = {"cadence": share >= TOL["within_min"], "late": late <= TOL["late_max"],
                     "work_p90": p90 <= TOL["work_p90_max_s"], "work_max": top <= TOL["work_max_max_s"]}
    return out


def integrity(o: dict) -> dict:
    """Section 4a's output-integrity row from the line itself: True, False, or None where it was not measured."""
    h = o.get("health") or {}
    zero = lambda k: (h[k] == 0) if h.get(k) is not None else None        # noqa: E731
    return {"dropped_0": zero("dropped"), "write_fails_0": zero("fails"), "not_switched_off": (not h.get("off")) if h else None,
            "counts_add_up": (sum(o[k] for k in fs.CLASSES) == o["watched_minutes"]) if "watched_minutes" in o else None}


def verdict(checks: dict) -> str:
    """'outside: a, b' when any check failed; else 'not measured: c' when any is unknown; else 'inside'."""
    bad, unknown = [k for k, v in checks.items() if v is False], [k for k, v in checks.items() if v is None]
    return ("outside: " + ", ".join(bad)) if bad else ("not measured: " + ", ".join(unknown)) if unknown else "inside"


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


def compare(session: str, db: Path = fs.DB, ops: Path = OPS, fetch=finished, gaps: Path = PACE) -> dict:
    looks, live, health = recorded(db, session)
    first, close = fs.session_minutes(session)
    symbols = sorted(looks)[:MAX_SYMBOLS]
    longs = longs_of(ops, session)
    out = {"session": session, "version": fs.VERSION, "made_at": datetime.now(timezone.utc).isoformat(),
           "symbols": len(symbols), "symbols_not_compared": max(0, len(looks) - MAX_SYMBOLS),
           "looks": sum(len(v) for v in looks.values()), "health": health,
           "longs_entered": len(longs), "pace": pace(session, gaps)}      # the pass does more work with more longs
    if not symbols:
        return {**out, "integrity": integrity(out), "note": "no look was recorded for this session"}
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
    out["integrity"] = integrity(out)
    stops = []
    for t in longs:
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


def pace_words(o: dict) -> str:
    """The frozen check in words, each figure beside its limit. An early close or a closed day is not the comparison
    session of section 4a and is labelled; the limits of the comparison are in the line's ``pace.limits``."""
    p, i = o.get("pace"), o.get("integrity")
    if not p:
        return ""
    t = p["tolerance"]
    if p.get("note"):
        figures = p["note"]
    else:
        figures = (f"{p['passes']} passes, {100 * p['within']:.2f}% started within 1.1 x 15 s (limit at least "
                   f"{100 * t['within_min']:.1f}%), late {p['late']} (limit {t['late_max']}), longest pass per 10 minutes: "
                   f"90th percentile {p['work_p90_s']} s (limit {t['work_p90_max_s']}), maximum {p['work_max_s']} s "
                   f"(limit {t['work_max_max_s']}), {o.get('longs_entered', 0)} longs entered")
    return (f"; frozen check, long book {p['window_et']} ET" + ("" if p["full_session"] else " (not a full session)")
            + f": {figures}; timing {verdict(p['inside'])}" + (f"; integrity {verdict(i)}" if i else ""))


def words(o: dict) -> str:
    if o.get("note"):
        return f"feed shadow {o['session']}: {o['note']}" + pace_words(o)
    w = o["no_live_candle_was"]
    lag = o["lag_hi_s"]
    return (f"feed shadow {o['session']} ({o['version']}): {o['symbols']} symbols, {o['looks']} looks, "
            f"{o['watched_minutes']} watched minutes; no live candle in {o['no_live_candle']} "
            f"(no trade on the tape {w['no_trade_on_tape']}, tape only {w['tape_only']}, never held {w['never_held']}); "
            f"delivered {o['delivered']} (held by: median {lag['median']} s, p95 {lag['p95']} s, max {lag['max']} s); "
            f"free feed had a candle in {o['free_feed_share_of_tape_minutes']} of tape-traded minutes; "
            f"price changed {o['changed']} (volume {o['volume_changed']}), free not on tape {o['free_not_on_tape']}, outside tape {o['outside_tape']}; "
            f"failed symbols {len(o['symbols_failed'])}; recorder dropped {o['health'].get('dropped')}, "
            f"write fails {o['health'].get('fails')}"
            + (f"; RECORDER OFF since {o['health']['off'].get('at') or 'an unrecorded time'}: "
               f"{o['health']['off'].get('why')}" if o["health"].get("off") else "")
            + pace_words(o))


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
