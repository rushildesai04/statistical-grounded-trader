"""Replay lab: our own rules replayed on real one-minute bars, as one HTML page to look at (owner 2026-11-17).

    python scripts/lab.py MPU 2026-11-17 --alert 10:02:02 --type gaining_momentum   # one stock, one day, one alert
    python scripts/lab.py NVDA 2026-11-14                                           # no alert: what the detectors saw
    python scripts/lab.py --random 20 [--seed 7]                                    # 20 random past alerts (tested events)
    python scripts/lab.py --events day.json                                         # a list: see EVENTS below
    python scripts/lab.py --site --out reports/charts/lab.html                      # runner job: every alerted stock of
                                                                                    # the last two sessions (ops DB)
    ... [--out reports/lab/lab.html]

EVENTS file: ``[{"symbol": "MPU", "session": "2026-11-17", "alerts": [{"at": "2026-11-17T15:02:02Z", "type":
"gaining_momentum"}], "live": "short 1.54, covered 1.70 on the timer"}]``. Today's session works too: official bars
are free once 15 minutes old, so the page shows the tape up to about a quarter of an hour ago.

Read-only: it downloads bars, replays ``sgt.lab.replay`` and writes one file. No order, no text, no database write.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from sgt.lab import page, replay
from sgt.ops import pagefile

ET = ZoneInfo("America/New_York")
TESTED = Path("reports/telegram/trades_pooled_first.csv")
OUT = Path("reports/lab/lab.html")
SYMBOL = re.compile(r"[A-Z][A-Z0-9.]{0,9}")
MAX_EVENTS, MAX_FILE = 80, 2_000_000


def spec(symbol: str, session: str, alerts: list[dict] | None = None, live: str | None = None) -> dict:
    """One validated request; raises on a symbol or date that is not one."""
    sym = str(symbol).upper()
    if not SYMBOL.fullmatch(sym):
        raise ValueError(f"not a ticker: {symbol!r}")
    day = date.fromisoformat(str(session)[:10]).isoformat()
    out = []
    for a in (alerts or [])[:replay.MAX_ALERTS]:
        at = pd.Timestamp(a["at"])
        at = at.tz_localize("UTC") if at.tzinfo is None else at.tz_convert("UTC")
        out.append({"at": at.isoformat(), "type": str(a.get("type") or "alert")[:replay.MAX_TEXT]})
    return {"symbol": sym, "session": day, "alerts": out, "live": None if live is None else str(live)[:300]}


def alert_at(session: str, hms: str) -> str:
    """'10:02' or '10:02:02' Eastern on that session -> UTC ISO instant."""
    parts = [int(x) for x in hms.split(":")]
    if len(parts) not in (2, 3):
        raise ValueError("alert time is HH:MM or HH:MM:SS (Eastern)")
    d = date.fromisoformat(session)
    return datetime(d.year, d.month, d.day, *parts, tzinfo=ET).astimezone(timezone.utc).isoformat()


def random_specs(n: int, seed: int | None, path: Path = TESTED) -> list[dict]:
    """``n`` random alerts the fade test traded (symbol, day, alert time and type as stored)."""
    ev = pd.read_csv(path, usecols=["symbol", "alert_type", "posted_at", "session", "status"])
    ev = ev[ev.status == "ok"]
    idx = random.Random(seed).sample(range(len(ev)), min(n, len(ev), MAX_EVENTS))
    return [spec(r.symbol, r.session, [{"at": r.posted_at, "type": r.alert_type}])
            for r in ev.iloc[sorted(idx)].itertuples()]


def from_file(path: Path) -> list[dict]:
    if path.stat().st_size > MAX_FILE:
        raise ValueError("events file too large")
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise ValueError("events file must hold a list")
    return [spec(r["symbol"], r["session"], r.get("alerts"), r.get("live")) for r in rows[:MAX_EVENTS]]


def site_specs(db=None) -> list[dict]:
    """The runner's daily list (``sgt.lab.day``), each request validated like any other; a row that is not a ticker
    or a date is dropped, never fatal."""
    from sgt.lab import day
    from sgt.ops.store import OpsStore

    out = []
    for r in day.specs(db or OpsStore()):
        try:
            out.append(spec(r["symbol"], r["session"], r["alerts"], r["live"]))
        except (ValueError, KeyError, TypeError):
            continue
    return out[:MAX_EVENTS]


def build(specs: list[dict], bars_fn, now: datetime) -> list[dict]:
    """Replays for the requests that have bars; a request without bars is reported and skipped."""
    out = []
    for s in specs:
        f = bars_fn(s["symbol"], s["session"])
        if f is None or not len(f):
            print(f"{s['symbol']} {s['session']}: no bars (not a session, unknown ticker, or too recent)")
            continue
        try:
            out.append(replay.replay(f, s["symbol"], s["session"], s["alerts"], s["live"]))
        except ValueError as e:
            print(f"{s['symbol']} {s['session']}: skipped ({e})")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", nargs="?")
    ap.add_argument("session", nargs="?", help="YYYY-MM-DD")
    ap.add_argument("--alert", help="alert time, Eastern (HH:MM or HH:MM:SS)")
    ap.add_argument("--type", default="volume_spike", help="alert type (default volume_spike)")
    ap.add_argument("--random", type=int, metavar="N")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--events", type=Path)
    ap.add_argument("--site", action="store_true", help="the latest sessions' alerted stocks from the ops database")
    ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args(argv)
    started = datetime.now(timezone.utc)                    # before the first read: the age of this build's data
    specs: list[dict] = []
    if a.events:
        specs += from_file(a.events)
    if a.site:
        specs += site_specs()
    if a.random:
        specs += random_specs(a.random, a.seed)
    if a.symbol:
        if not a.session:
            ap.error("a ticker needs a day (YYYY-MM-DD)")
        alerts = [{"at": alert_at(a.session, a.alert), "type": a.type}] if a.alert else []
        specs.append(spec(a.symbol, a.session, alerts))
    if not specs:
        if a.site:                                          # no alert yet: an empty page, not a failed job
            pagefile.publish(a.out, page.render([], started), started)
            print(f"0 replay(s) -> {a.out.resolve()}")
            return 0
        ap.error("nothing to replay: give a ticker and a day, --random N or --events FILE")

    from fade_live import data_client
    from trade_charts import session_bars

    now = datetime.now(timezone.utc)
    with data_client() as client:
        replays = build(specs, lambda sym, day: session_bars(client, sym, day, now), now)
    # 2026-11-24: in one step, from a file of its own, and never over a page built from newer data (two lab builds
    # overlapped at the 16:14 ET deploy and both wrote straight into the page: sgt.ops.pagefile).
    done = pagefile.publish(a.out, page.render(replays, now), started)
    print(f"{len(replays)} replay(s) -> {a.out.resolve()}" + ("" if done else " (not written: a newer page is in place)"))
    return 0 if replays or a.site else 1


if __name__ == "__main__":
    raise SystemExit(main())
