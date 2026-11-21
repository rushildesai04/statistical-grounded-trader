"""The stock-loan list as a scanner: shares drying up, fees jumping (docs/findings/prereg_borrow_scan_forward_2026-11-18.md).

    python scripts/borrow_scan.py record     # nightly on the runner: find and store the events of finished sessions
    python scripts/borrow_scan.py count      # how many events so far (no outcome)
    python scripts/borrow_scan.py decide     # ONCE, refused before 300 events of one kind have a 5-session result

Runs where the lists are (the runner's volume). Forward only; report only: nothing here changes a book. Until the
decision only COUNTS are read. Bars come from an external API: parsed strictly, bounded, with a timeout."""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from after_day_borrow_study import closing_snapshot

ET = ZoneInfo("America/New_York")
OUT = Path("reports/borrow_scan")
BORROW = Path("data/borrow")
FIRST = "2026-11-04"                           # first session with stored lists
DRY_FROM, DRY_TO, DRY_DROP = 100_000, 10_000, 0.90
FEE_X, FEE_MIN = 2.0, 20.0
PX_MIN, DV_MIN = 2.0, 2_000_000.0
COOLDOWN = 10                                  # sessions: a symbol counts once per kind in this many
HOLDS = (1, 5)
COST, MIN_EVENTS = 0.0030, 300
ALPHA, SEED, N_BOOT = 0.05 / 8, 20261118, 10_000
CHUNK, MAX_CANDIDATES, THROTTLE_S = 100, 3_000, 1.0
KINDS = ("DRY", "FEE")
SYM = re.compile(r"[A-Z]{1,5}(\.[A-Z])?")       # the list is external text: only plain tickers go into a request
COLS = ["symbol", "session", "kind", "avail_prev", "avail", "fee_prev", "fee", "close", "dollar_volume"]


def candidates(prev: pd.DataFrame, cur: pd.DataFrame) -> pd.DataFrame:
    """Symbols whose closing list changed like an event, before the price and volume screen. Both frames: indexed
    by symbol with ``avail`` and ``fee`` (the previous and the current session's closing list)."""
    j = prev.join(cur, how="inner", lsuffix="_prev")
    a0, a1 = j.avail_prev.astype(float), j.avail.astype(float)
    f0, f1 = j.fee_prev.astype(float), j.fee.astype(float)
    dry = (a0 >= DRY_FROM) & (a1 < DRY_TO) & (a1 <= (1 - DRY_DROP) * a0)
    fee = (f0 > 0) & (f1 >= FEE_X * f0) & (f1 >= FEE_MIN)
    out = []
    for kind, m in (("DRY", dry), ("FEE", fee)):
        x = j[m.fillna(False)]
        out.append(pd.DataFrame({"symbol": x.index, "kind": kind, "avail_prev": x.avail_prev.to_numpy(float),
                                 "avail": x.avail.to_numpy(float), "fee_prev": x.fee_prev.to_numpy(float), "fee": x.fee.to_numpy(float)}))
    return pd.concat(out, ignore_index=True)


def sessions(first: str, last: date) -> list[str]:
    from sgt.intraday.calendar import default_calendar

    cal, d, out = default_calendar(), date.fromisoformat(first), []
    while d <= last:
        if cal.is_session(d):
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def parse_daily(payload, day: str) -> dict[str, tuple[float, float]]:
    """symbol -> (close, volume) of ``day`` from a multi-symbol daily-bars payload; anything malformed is dropped."""
    bars = payload.get("bars") if isinstance(payload, dict) else None
    out = {}
    for sym, rows in (bars.items() if isinstance(bars, dict) else ()):
        for x in rows if isinstance(rows, list) else ():
            if (isinstance(x, dict) and str(x.get("t", ""))[:10] == day
                    and all(isinstance(x.get(k), (int, float)) and not isinstance(x.get(k), bool) for k in ("c", "v"))
                    and x["c"] > 0 and x["v"] >= 0):
                out[str(sym)] = (float(x["c"]), float(x["v"]))
    return out


def day_bars(client, url: str, headers: dict, symbols: list[str], day: str) -> dict[str, tuple[float, float]] | None:
    """Close and volume of ``day`` for the symbols (raw bars). None when a request failed (said in the log): the day
    is tried again. A symbol that is not a plain ticker is never sent: it has no bar, so it is no event. The range is
    that UTC day, spelled out (2026-11-20: a bare date as the end is read as the END of that day in New York, which on
    the evening after the session is still in the future; the plan refuses that with HTTP 403, so the scan ran one
    session behind and its job failed every night)."""
    out: dict[str, tuple[float, float]] = {}
    symbols = [s for s in symbols if isinstance(s, str) and SYM.fullmatch(s)]
    for i in range(0, len(symbols), CHUNK):
        try:
            r = client.get(url, headers=headers, params={"symbols": ",".join(symbols[i:i + CHUNK]), "timeframe": "1Day",
                                                         "start": f"{day}T00:00:00Z", "end": f"{day}T23:59:59Z",
                                                         "adjustment": "raw", "feed": "sip", "limit": 10000})
            why = None if r.status_code == 200 else f"HTTP {r.status_code}"
            j = r.json() if why is None else None
        except Exception as e:  # noqa: BLE001 - network or JSON: logged, and the day is tried again another night
            why, j = type(e).__name__, None
        if why is None and (not isinstance(j, dict) or j.get("next_page_token")):
            why = "an answer that is not one complete page"
        if why is not None:
            print(f"borrow scan: the bars of {day} could not be read ({why}); that session waits for the next run")
            return None
        out.update(parse_daily(j, day))
        time.sleep(THROTTLE_S)
    return out


def screen(cand: pd.DataFrame, bars: dict[str, tuple[float, float]], session: str, past: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    """Events of one session: candidates that pass the price and dollar-volume screen and the cooldown."""
    pos = {s: k for k, s in enumerate(order)}
    rows = []
    for r in cand.itertuples(index=False):
        b = bars.get(r.symbol)
        if b is None or b[0] < PX_MIN or b[0] * b[1] < DV_MIN:
            continue
        earlier = past[(past.symbol == r.symbol) & (past.kind == r.kind)].session
        if any(0 < pos[session] - pos.get(s, -10**6) <= COOLDOWN for s in earlier):
            continue
        rows.append({**r._asdict(), "session": session, "close": b[0], "dollar_volume": b[0] * b[1]})
    return pd.DataFrame(rows, columns=COLS)


def load(out: Path = OUT) -> tuple[pd.DataFrame, list[str]]:
    ev = pd.read_csv(out / "events.csv", converters={"symbol": str}) if (out / "events.csv").is_file() else pd.DataFrame(columns=COLS)
    done = json.loads((out / "sessions.json").read_text()) if (out / "sessions.json").is_file() else []
    return ev, [str(s) for s in done if isinstance(s, str)]


def record(today: date, bars_fn, out: Path = OUT, root: Path = BORROW) -> dict:
    """Process, in order, every finished session not processed yet (the list of the day against the session before).
    A failed bar request stops the run there (``waiting``): later sessions wait too, so the once-in-10-sessions
    rule always sees the earlier events. A pair with more than MAX_CANDIDATES changes is a broken list: no events."""
    ev, done = load(out)
    order = sessions(FIRST, today - timedelta(days=1))          # today's daily bar is not served yet
    new, waiting, no_list, broken = 0, None, [], []
    for prev, cur in zip(order, order[1:]):
        if cur in done:
            continue
        a, b = closing_snapshot(prev, root), closing_snapshot(cur, root)
        if a is None or b is None:
            no_list.append(cur)                                 # a list is missing: that day has no events
            continue
        cand = candidates(a, b)
        if len(cand) > MAX_CANDIDATES:
            print(f"borrow scan: {cur} has {len(cand)} changes (over {MAX_CANDIDATES}): a broken list, no events counted")
            broken.append(cur)
            done.append(cur)
            continue
        bars = bars_fn(sorted(set(cand.symbol)), cur) if len(cand) else {}
        if bars is None:
            waiting = cur
            break
        got = screen(cand, bars, cur, ev, order)
        ev = pd.concat([ev, got], ignore_index=True) if len(got) else ev
        done.append(cur)
        new += len(got)
    out.mkdir(parents=True, exist_ok=True)
    ev.to_csv(out / "events.csv", index=False)
    (out / "sessions.json").write_text(json.dumps(sorted(done)) + "\n")
    if broken:
        old = json.loads((out / "broken.json").read_text()) if (out / "broken.json").is_file() else []
        (out / "broken.json").write_text(json.dumps(sorted(set(old) | set(broken))) + "\n")
    return {"new_events": int(new), "waiting": waiting, "sessions_without_a_list": len(no_list), "broken_lists": broken,
            **count(ev, order)}


def count(ev: pd.DataFrame, order: list[str]) -> dict:
    """Events per kind, and how many are old enough to have a 5-session result. No outcome is read."""
    pos = {s: k for k, s in enumerate(order)}
    last = len(order) - 1
    ripe = ev.session.map(lambda s: s in pos and last - pos[s] >= max(HOLDS)) if len(ev) else pd.Series([], dtype=bool)
    return {"sessions": len(order), **{f"{k}_events": int((ev.kind == k).sum()) for k in KINDS},
            **{f"{k}_with_5_session_result": int(((ev.kind == k) & ripe).sum()) for k in KINDS}, "needed": MIN_EVENTS}


def outcome(ev: pd.DataFrame, closes_of, spy: pd.Series) -> pd.DataFrame:
    """Per event and hold: the stock's return minus SPY's from the event day's close (long and short, after costs;
    the short also pays the fee at the event for the days held)."""
    rows = []
    for r in ev.itertuples(index=False):
        c = closes_of(r.symbol)
        if c is None or r.session not in c.index or r.session not in spy.index:
            continue
        i, k = c.index.get_loc(r.session), spy.index.get_loc(r.session)
        for h in HOLDS:
            if i + h >= len(c) or k + h >= len(spy) or c.index[i + h] != spy.index[k + h]:
                continue
            rel = (c.iloc[i + h] / c.iloc[i] - 1) - (spy.iloc[k + h] / spy.iloc[k] - 1)
            days = (pd.Timestamp(c.index[i + h]) - pd.Timestamp(r.session)).days
            fee = 0.0 if pd.isna(r.fee) else float(r.fee) / 100 * days / 360
            rows.append({"symbol": r.symbol, "session": r.session, "kind": r.kind, "hold": h, "long": rel - COST, "short": -rel - COST - fee})
    return pd.DataFrame(rows)


def decide(o: pd.DataFrame) -> dict:
    res, rng = {}, np.random.default_rng(SEED)
    for kind in KINDS:
        for h in HOLDS:
            g = o[(o.kind == kind) & (o.hold == h)]
            u, inv = np.unique(g.session.to_numpy(), return_inverse=True) if len(g) else (np.array([]), np.array([], int))
            for side in ("long", "short"):
                x = g[side].to_numpy(float)
                lo = None
                if len(u) >= 2:
                    s, n = np.bincount(inv, x, len(u)), np.bincount(inv, minlength=len(u)).astype(float)
                    b = rng.integers(0, len(u), size=(N_BOOT, len(u)))
                    lo = float(np.quantile(s[b].sum(1) / n[b].sum(1), ALPHA))
                res[f"{kind} | {h} | {side}"] = {"events": int(len(x)), "mean": float(x.mean()) if len(x) else None, "lower": lo,
                                                 "pass": bool(lo is not None and lo > 0)}
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("record", "count", "decide"))
    a = ap.parse_args(argv)
    today = datetime.now(ET).date()
    if a.cmd == "count":
        ev, _ = load()
        print(json.dumps(count(ev, sessions(FIRST, today - timedelta(days=1))), indent=1))
        return 0
    import httpx

    from sgt.config import get_settings
    from sgt.intraday.sources import _alpaca_headers

    url = get_settings().alpaca_data_url.rstrip("/") + "/v2/stocks/bars"
    with httpx.Client(timeout=30) as client:
        if a.cmd == "record":
            res = record(today, lambda syms, day: day_bars(client, url, _alpaca_headers(), syms, day))
            print("borrow scan: " + json.dumps(res))
            return 1 if res["waiting"] else 0                   # non-zero: the job wrapper texts that the scan is behind
        res_path = OUT / "result.json"
        if res_path.is_file():
            print(f"{res_path} exists: the decision was made once and is not repeated")
            return 2
        ev, _ = load()
        n = count(ev, sessions(FIRST, today - timedelta(days=1)))
        if max(n[f"{k}_with_5_session_result"] for k in KINDS) < MIN_EVENTS:
            print(json.dumps({"count": n, "note": "no outcome is computed before the count is reached"}, indent=1))
            return 3
        import alert_after_day_study as base

        end = today - timedelta(days=1)
        base.CACHE, base.END = Path("data/borrow_scan_daily") / end.isoformat(), end.isoformat()
        memo: dict[str, pd.Series | None] = {}

        def closes_of(sym: str):
            if sym not in memo:
                d = base.fetch(sym)
                memo[sym] = None if d is None else d.close
            return memo[sym]
        res = {"prereg": "docs/findings/prereg_borrow_scan_forward_2026-11-18.md", "count": n,
               "cells": decide(outcome(ev, closes_of, closes_of("SPY")))}
        OUT.mkdir(parents=True, exist_ok=True)
        res_path.write_text(json.dumps(res, indent=1) + "\n")
        print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
