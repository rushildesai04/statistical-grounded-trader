"""Score the pre-registered MetaTradings QQQ 0DTE option-call test ONCE.

Rules: docs/findings/prereg_options_ideas_2026-10-30.md.

Usage:
    python scripts/options_ideas_test.py --dry-run    # fetch/cache bars, coverage only; no returns
    python scripts/options_ideas_test.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.calendar import default_calendar
from sgt.signals_live.ideas import OptionIdea, parse_idea
from telegram_alert_test import boot_means

ET = "America/New_York"
CHANNEL = "-1001204455879"
SEED = 20261030
OUT = Path("reports/options_ideas")
CACHE = Path("data/options/bars_1m")
LIMIT_MULT, LIVE_MIN = 1.05, 10
TP_A, TP_B, STOP = 1.20, 2.00, 0.50
EXIT_TOD = 15 * 60 + 50
SPREAD_SIDE, FEE_CONTRACT_SIDE = 0.02, 0.03     # $/share half-spread, $/contract fees, each side
TIMEOUT_S = 20.0


def occ_symbol(idea: OptionIdea, expiry: date) -> str:
    return f"{idea.underlying}{expiry:%y%m%d}{idea.right}{round(idea.strike * 1000):08d}"


def load_calls(db: str) -> pd.DataFrame:
    with sqlite3.connect(db) as c:
        df = pd.read_sql("SELECT message_id, posted_at, raw_text FROM telegram_messages "
                         "WHERE channel = ? AND edited_at IS NULL", c, params=(CHANNEL,))
    df["idea"] = df.raw_text.map(parse_idea)
    df = df[df.idea.map(lambda x: isinstance(x, OptionIdea) and x.dte == 0)].copy()
    df["posted_at"] = pd.to_datetime(df.posted_at, utc=True, format="ISO8601")
    et = df.posted_at.dt.tz_convert(ET)
    df["session"] = et.dt.date
    tod = et.dt.hour * 60 + et.dt.minute
    cal = default_calendar()
    df = df[df.session.map(cal.is_session) & (tod >= 9 * 60 + 30) & (tod < 15 * 60)]
    df = df.sort_values("posted_at").drop_duplicates("session").reset_index(drop=True)
    df["contract"] = [occ_symbol(i, d) for i, d in zip(df.idea, df.session)]
    return df


def fetch_bars(contract: str, day: date, headers: dict) -> pd.DataFrame:
    """1-minute option trade bars for one contract-day, cached as parquet (bounded, fail closed)."""
    path = CACHE / f"{contract}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    url = "https://data.alpaca.markets/v1beta1/options/bars"
    params = {"symbols": contract, "timeframe": "1Min", "start": f"{day}T08:00:00Z",
              "end": f"{day}T21:00:00Z", "limit": 10000}
    rows: list = []
    for _ in range(10):
        for attempt in range(6):   # 429: shared 200 req/min quota (other backfills) -> back off
            r = httpx.get(url, params=params, headers=headers, timeout=TIMEOUT_S)
            if r.status_code != 429:
                break
            time.sleep(5 * 2 ** attempt)
        time.sleep(0.35)
        if r.status_code >= 400:
            raise RuntimeError(f"option bars HTTP {r.status_code} {contract}: {r.text[:200]}")
        j = r.json()
        rows.extend((j.get("bars") or {}).get(contract) or [])
        if not j.get("next_page_token"):
            break
        params["page_token"] = j["next_page_token"]
    else:
        raise RuntimeError(f"option bars pagination did not finish: {contract}")
    f = pd.DataFrame(rows, columns=["t", "o", "h", "l", "c", "v"]).rename(
        columns={"t": "ts", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    f["ts"] = pd.to_datetime(f.ts, utc=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    f.to_parquet(path)
    return f


def _with_tod(f: pd.DataFrame) -> pd.DataFrame:
    f = f.sort_values("ts").reset_index(drop=True)
    et = f.ts.dt.tz_convert(ET)
    return f.assign(tod=et.dt.hour * 60 + et.dt.minute)


def _walk(f: pd.DataFrame, start: int, fill: float, tps: list[float | None]) -> list[float]:
    """Exit price per leg from bar ``start`` on: stop first, then take-profits, else the 15:50 open."""
    stop = fill * STOP
    out: list[float | None] = [None] * len(tps)
    i = start
    while i < len(f) and f.tod.iloc[i] < EXIT_TOD:
        o, h, lo = f.open.iloc[i], f.high.iloc[i], f.low.iloc[i]
        if lo <= stop:
            return [min(stop, o) if x is None else x for x in out]
        for k, tp in enumerate(tps):
            if out[k] is None and tp is not None and h >= fill * tp:
                out[k] = fill * tp
        if all(x is not None for x in out):
            return out
        i += 1
    last = f.open.iloc[i] if i < len(f) else f.close.iloc[-1]
    return [last if x is None else x for x in out]


def _net(fill: float, exits: list[float], cost_mult: float = 1.0) -> float:
    cost = cost_mult * (2 * SPREAD_SIDE + 2 * FEE_CONTRACT_SIDE / 100)
    return float((np.mean(exits) - fill - cost) / fill)


RULES = {"R1": [TP_A, TP_B], "R2": [TP_A], "R3": [None]}


def simulate(premium: float, posted: pd.Timestamp, f: pd.DataFrame) -> dict:
    if f is None or f.empty:
        return {"status": "no_bars"}
    f = _with_tod(f)
    start = posted.floor("min") + pd.Timedelta(minutes=1)
    win = f[(f.ts >= start) & (f.ts < start + pd.Timedelta(minutes=LIVE_MIN))]
    limit = premium * LIMIT_MULT
    hit = win[win.low <= limit]
    if hit.empty:
        return {"status": "no_fill", "first_open": float(win.open.iloc[0]) if len(win) else None}
    j = hit.index[0]
    fill = float(min(limit, f.open.loc[j]))
    out = {"status": "filled", "fill": fill, "fill_ts": str(f.ts.loc[j]), "slip_vs_posted": fill / premium - 1}
    for name, tps in RULES.items():
        ex = _walk(f, j + 1, fill, tps)
        out[name] = _net(fill, ex)
        out[f"{name}_2x"] = _net(fill, ex, 2.0)
    # R4: R1 as if filled at the posted premium at the same bar (advertised vs achievable).
    ex4 = _walk(f, j + 1, premium, RULES["R1"])
    out["R4"] = _net(premium, ex4)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default="sgt.db")
    ap.add_argument("--dry-run", action="store_true", help="fetch + coverage only; no returns")
    args = ap.parse_args()
    from sgt.intraday.sources import _alpaca_headers

    calls = load_calls(args.db)
    headers = _alpaca_headers()
    bars = {r.contract: fetch_bars(r.contract, r.session, headers) for r in calls.itertuples()}
    have = sum(1 for b in bars.values() if not b.empty)
    print(f"eligible calls {len(calls)} ({calls.session.min()} .. {calls.session.max()}); "
          f"with option bars {have} ({have / len(calls):.1%})")
    invalid = have < 0.9 * len(calls)
    if args.dry_run:
        print("INVALID: > 10% without bars" if invalid else "coverage OK")
        return 0
    if invalid:
        print("INVALID (no verdict): > 10% of calls without option bars")
        return 1

    res = pd.DataFrame([{"session": r.session, "contract": r.contract, "right": r.idea.right,
                         **simulate(r.idea.premium, r.posted_at, bars[r.contract])}
                        for r in calls.itertuples()])
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / "calls.csv", index=False)
    print(res.status.value_counts().to_string())
    t = res[res.status == "filled"].sort_values("session").reset_index(drop=True)
    x = t.R1.to_numpy(float)
    rng = np.random.default_rng(SEED)
    lb = float(np.quantile(boot_means(x, t.session.astype(str).to_numpy(), rng), 0.05))
    half = len(t) // 2
    h1, h2 = float(t.R1.iloc[:half].mean()), float(t.R1.iloc[half:].mean())
    checks = {"lower95>0": lb > 0, "2x_cost_mean>0": bool(t.R1_2x.mean() > 0), "both_halves>0": h1 > 0 and h2 > 0}
    verdict = "PASS" if all(checks.values()) else "FAIL"
    print(f"\nR1 (posted plan) n={len(t)}  mean net {x.mean():+.2%}  median {np.median(x):+.2%}  "
          f"win {np.mean(x > 0):.0%}  lower95 {lb:+.2%}  2x-cost {t.R1_2x.mean():+.2%}  halves {h1:+.2%} / {h2:+.2%}")
    print("checks:", checks, f"\nVERDICT: {verdict}")
    sec = {k: float(t[k].mean()) for k in ("R2", "R3", "R4")}
    by_right = t.groupby("right").R1.agg(["count", "mean"]).to_dict("index")
    print(f"SECONDARY: R2 +20% all {sec['R2']:+.2%} | R3 hold+stop {sec['R3']:+.2%} | "
          f"R4 at posted premium {sec['R4']:+.2%} | fill vs posted {t.slip_vs_posted.mean():+.1%} | by right {by_right}")

    summary = {"verdict": verdict, "checks": checks, "n": len(t), "mean": float(x.mean()), "median": float(np.median(x)),
               "win": float(np.mean(x > 0)), "lower95": lb, "halves": [h1, h2], "secondary": sec,
               "by_right": by_right, "status": res.status.value_counts().to_dict()}
    (OUT / "result.json").write_text(json.dumps(summary, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="options-ideas-2026-10-30", kind="options-ideas",
                                  params={"prereg": "docs/findings/prereg_options_ideas_2026-10-30.md"},
                                  metrics=json.loads(json.dumps(summary, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
