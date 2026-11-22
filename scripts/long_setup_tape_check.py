"""Did each recorded long setup exist on the full tape? Retrospective diagnostic (docs/findings/
prereg_long_setup_tape_2026-11-22.md). No return is computed and nothing is written outside ``reports/long_setup_tape``.

    python scripts/long_setup_tape_check.py control ROWS.json      # step 1: the free feed only
    python scripts/long_setup_tape_check.py tape ROWS.json EVENTS.json   # step 2: once (guarded)

ROWS.json: the ``long_trades`` rows with their alert's ``posted_at``; EVENTS.json: the first qualifying alerts
(id, symbol, session, posted_at). Both are read-only exports of the runner's ops database.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from sgt.fast import long_book, long_shadow
from sgt.fast import patterns as pt

OUT = Path("reports/long_setup_tape")
ET = ZoneInfo("America/New_York")
COLS = ["ts", "open", "high", "low", "close", "volume"]
THROTTLE_S = 1.0                    # the runner trades on this data account: stay far below its budget
MAX_ROWS = 1_000                    # one regular session is 390 minutes
STOP_TOL = 0.005                    # half a cent: "the same stop" in the control
MOVED = 0.25                        # a tape stop this share of the recorded risk away from the feed's is "moved"


def parse_bars(rows) -> pd.DataFrame:
    """Whole, numeric bars only (external bytes), time-ordered, at most MAX_ROWS."""
    ok = [x for x in (rows if isinstance(rows, list) else [])[:MAX_ROWS]
          if isinstance(x, dict) and isinstance(x.get("t"), str)
          and all(isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool) for k in "ohlcv")]
    f = pd.DataFrame({"ts": pd.to_datetime([x["t"] for x in ok], utc=True, errors="coerce"),
                      **{n: [float(x[k]) for x in ok] for k, n in zip("ohlcv", COLS[1:])}}, columns=COLS)
    f = f.dropna(subset=["ts"])
    return f[(f[["open", "high", "low", "close"]] > 0).all(axis=1)].sort_values("ts").reset_index(drop=True)


def bars(client, url: str, headers: dict, symbol: str, session: str, feed: str) -> pd.DataFrame | None:
    """One stock-day's regular session on one feed, cached. None = the request failed (no class is guessed from it)."""
    p = OUT / "bars" / feed / f"{symbol}_{session}.json"
    if p.is_file():
        return parse_bars(json.loads(p.read_text()))
    d = date.fromisoformat(session)
    start, end = (datetime.combine(d, datetime.min.time(), ET).replace(hour=h, minute=m) for h, m in ((9, 30), (16, 0)))
    time.sleep(THROTTLE_S)
    try:
        r = client.get(f"{url}/v2/stocks/{symbol}/bars", headers=headers, params={
            "timeframe": "1Min", "feed": feed, "limit": MAX_ROWS, "adjustment": "raw",
            "start": start.isoformat(), "end": end.isoformat()})
        j = r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001 - network or JSON: reported as no data, never as a class
        return None
    if not isinstance(j, dict):
        return None
    raw = [x for x in (j.get("bars") or []) if isinstance(x, dict)][:MAX_ROWS]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(raw))
    f = parse_bars(raw)
    return f[f.ts < pd.Timestamp(end)]


def latest(session: str) -> pd.Timestamp:
    return pd.Timestamp(f"{session} 15:45", tz=ET).tz_convert("UTC")


def found(f: pd.DataFrame, posted_at: str) -> tuple[list[pt.Setup], pd.Timestamp | None, bool]:
    """Every setup the detectors find for this alert on ``f`` (all kinds), its start bar, and whether the bars call
    the alert a halt (no bar within the entry window)."""
    row = {"posted_at": posted_at, "status": "live"}
    got, t0 = long_shadow.setups(f, row)
    if t0 is None:
        return [], None, False
    halt = t0 - pd.Timestamp(posted_at).tz_convert("UTC") > pd.Timedelta(long_shadow.fade.MAX_ENTRY_DELAY)
    return got + pt.base_break(f, t0), t0, bool(halt)


def filled(f: pd.DataFrame, s: pt.Setup, session: str) -> pd.Timestamp | None:
    """The minute the setup's order fills on ``f`` (a base break bought at its candle's close counts at ``ready``)."""
    t = pt.simulate(f, s, latest=latest(session))
    return None if t is None else t.entry_ts


def of_kind(f: pd.DataFrame, r: dict) -> tuple[list[tuple[pt.Setup, pd.Timestamp | None]], bool]:
    got, _, halt = found(f, r["posted_at"])
    return [(s, filled(f, s, r["session"])) for s in got if s.kind == r["kind"] and s.side == "long"], halt


def control(f: pd.DataFrame, r: dict) -> pt.Setup | None:
    """The recorded setup on the free feed's stored bars, or None when today's detector does not reproduce it."""
    at = pd.Timestamp(r["entry_ts"]).tz_convert("UTC")
    for s, fill in of_kind(f, r)[0]:
        same_time = fill == at or s.ready == at
        if same_time and abs(s.stop - float(r["stop"])) <= STOP_TOL:
            return s
    return None


def classify(r: dict, feed: pd.DataFrame | None, tape: pd.DataFrame | None) -> dict:
    """Part A: one class for one recorded setup."""
    out = {"id": r["id"], "symbol": r["symbol"], "session": r["session"], "kind": r["kind"], "status": r["status"],
           "lost": None if r.get("net") is None else bool(r["net"] < 0)}
    if feed is None or tape is None or tape.empty:
        return {**out, "class": "INSUFFICIENT EVIDENCE", "why": "no bars"}
    s0 = control(feed, r)
    if s0 is None:
        return {**out, "class": "INSUFFICIENT EVIDENCE", "why": "not reproduced on the feed's stored bars"}
    fill0 = filled(feed, s0, r["session"]) or s0.ready
    cands, halt_tape = of_kind(tape, r)
    _, halt_feed = of_kind(feed, r)
    out |= {"halt_feed": halt_feed, "halt_tape": halt_tape}
    live = [(s, t) for s, t in cands if t is not None]
    if not live:
        return {**out, "class": "THIN FEED ONLY", "why": "found on the tape, order cancelled or never triggered"
                if cands else "no such setup on the tape"}
    s1, t1 = min(live, key=lambda x: x[1])
    risk = abs(float(r["est_entry"] or 0) - float(r["stop"])) or None
    moved = None if not risk else bool(abs(s1.stop - s0.stop) > MOVED * risk)
    same = s1.ready == s0.ready and t1 == fill0
    return {**out, "class": "EXISTED" if same else "EXISTED, TIMING DIFFERED",
            "minutes": (t1 - fill0).total_seconds() / 60, "stop_feed": s0.stop, "stop_tape": s1.stop,
            "stop_moved": moved}


def pick(f: pd.DataFrame, e: dict) -> tuple[str, pd.Timestamp, float] | None:
    """Part B: the live rule's earliest setup on ``f`` (kind, minute, stop). Existence only: no cost check."""
    best = None
    for s in long_book.live_setups(f, {"posted_at": e["posted_at"], "status": "live"}):
        if s.side != "long":
            continue
        at = s.ready if s.trigger is None else filled(f, s, e["session"])
        if at is not None and at < latest(e["session"]) and (best is None or at < best[1]):
            best = (s.kind, at, s.stop)
    return best


def agree(e: dict, feed: pd.DataFrame | None, tape: pd.DataFrame | None) -> dict:
    out = {"id": e["id"], "symbol": e["symbol"], "session": e["session"]}
    if feed is None or tape is None:
        return {**out, "class": "NO DATA"}
    a, b = pick(feed, e), pick(tape, e)
    if a is None and b is None:
        return {**out, "class": "BOTH NONE"}
    if a is None:
        return {**out, "class": "TAPE ONLY", "tape": [b[0], b[1].isoformat()]}
    if b is None:
        return {**out, "class": "THIN FEED ONLY", "feed": [a[0], a[1].isoformat()]}
    same = a[0] == b[0] and a[1] == b[1]
    return {**out, "class": "SAME" if same else "DIFFERENT", "feed": [a[0], a[1].isoformat()],
            "tape": [b[0], b[1].isoformat()], "minutes": (b[1] - a[1]).total_seconds() / 60,
            "stop_feed": a[2], "stop_tape": b[2]}


def _client():
    import httpx

    from sgt.core.settings import get_settings
    from sgt.intraday.sources import _alpaca_headers
    return httpx.Client(timeout=30), get_settings().alpaca_data_url.rstrip("/"), _alpaca_headers()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2 or args[0] not in ("control", "tape"):
        print(__doc__)
        return 2
    rows = [r for r in json.loads(Path(args[1]).read_text()) if r.get("kind")]
    OUT.mkdir(parents=True, exist_ok=True)
    client, url, headers = _client()
    with client:
        if args[0] == "control":
            res = []
            for r in rows:
                f = bars(client, url, headers, r["symbol"], r["session"], "iex")
                s = None if f is None else control(f, r)
                res.append({"id": r["id"], "symbol": r["symbol"], "session": r["session"], "kind": r["kind"],
                            "bars": None if f is None else len(f), "reproduced": s is not None})
            (OUT / "control.json").write_text(json.dumps(res, indent=1))
            print("reproduced", sum(x["reproduced"] for x in res), "of", len(res))
            print([(x["id"], x["symbol"], x["kind"], x["bars"]) for x in res if not x["reproduced"]])
            return 0
        done = OUT / "result.json"
        if done.is_file():
            print("already run once:", done)
            return 1
        events = json.loads(Path(args[2]).read_text())
        a = [classify(r, bars(client, url, headers, r["symbol"], r["session"], "iex"),
                      bars(client, url, headers, r["symbol"], r["session"], "sip")) for r in rows]
        b = [agree(e, bars(client, url, headers, e["symbol"], e["session"], "iex"),
                   bars(client, url, headers, e["symbol"], e["session"], "sip")) for e in events]
    done.write_text(json.dumps({"recorded": a, "live_rule": b}, indent=1, default=str))
    for name, xs in (("A recorded setups", a), ("B live rule", b)):
        print(name, pd.Series([x["class"] for x in xs]).value_counts().to_dict())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
