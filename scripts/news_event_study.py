"""News-catalyst event study (2026-10-19): does a fresh headline on a small cap carry an
edge that price/volume rules do not?

Events are chosen from information available at the headline only: single-symbol,
non-recap, non-analyst headlines; the stock's PRIOR close in $1-30; no other headline on
that symbol in the previous 6 hours. Then, from 1-minute SIP bars around the event:
* headline minute h = the minute containing ``created_at``; its volume/return are known at
  its close ("news + volume" reaction: volume >= 5x the prior 30-minute mean, minute up);
* entry at the OPEN of minute h+1 (a reaction of seconds to ~1 minute);
* gross return to the close of h+5/h+15/h+30/h+60; net pays gapper time-of-day spreads.
Training period only (--before); sampled per category, seeded.

Usage:
    python scripts/news_event_study.py --before 2026-04-01 --per-category 1500
"""
from __future__ import annotations

import argparse
import glob
import logging
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.decision import validation as val
from sgt.decision.features import CostModelAdapter
from sgt.intraday.sources import AlpacaHistoricalBars, DataSourceError

log = logging.getLogger("news_event_study")
HORIZONS = (5, 15, 30, 60)
RECAP = (r"shares are trading|stock is (?:trading|moving|rising|falling)|shares (?:are )?(?:rising|falling|"
         r"moving|soaring|surging|plunging|jumping|down|up) |movers|stocks moving|why .* (?:stock|shares)|"
         r"what's going on|trading higher|trading lower|halted|session (?:gainers|losers)|mid-day|"
         r"premarket (?:gainers|losers)|52-week")
ANALYST = (r"maintains|price target|upgrades|downgrades|initiates coverage|reiterates|analyst|"
           r"expectations for|earnings preview|options activity|whale|short interest|p/e ratio|insights into")
CATS = [("fda_clinical", r"fda|clinical|phase [123i]|trial|topline|clearance|breakthrough designation|orphan drug"),
        ("deal", r"merger|acquire|acquisition|to be acquired|buyout|definitive agreement|go-private|tender offer"),
        ("contract", r"contract|award|partnership|collaborat|agreement|order from|purchase order|selected by"),
        ("offering", r"offering|private placement|registered direct|warrant|priced|atm program|dilut"),
        ("earnings", r"earnings|reports q[1-4]|quarterly results|eps|financial results|guidance")]


def load_events(before: str) -> pd.DataFrame:
    df = pd.concat([pd.read_parquet(p) for p in sorted(glob.glob("data/news/*.parquet"))], ignore_index=True)
    df = df[df.symbols.str.count(",") == 0].copy()
    h = df.headline.str.lower()
    df = df[~h.str.contains(RECAP) & ~h.str.contains(ANALYST)].copy()
    h = df.headline.str.lower()
    df["cat"] = "other"
    for name, pat in CATS:
        df.loc[h.str.contains(pat) & (df.cat == "other"), "cat"] = name
    df["ts"] = pd.to_datetime(df.created_at, utc=True)
    df = df[df.ts < pd.Timestamp(before, tz="UTC")].sort_values("ts")
    df["prev_ts"] = df.groupby("symbols")["ts"].shift(1)
    df = df[(df.prev_ts.isna()) | (df.ts - df.prev_ts > pd.Timedelta(hours=6))]
    et = df.ts.dt.tz_convert("America/New_York")
    mins = et.dt.hour * 60 + et.dt.minute
    return df[(mins >= 240) & (mins <= 1170)].rename(columns={"symbols": "symbol"})


def prior_close(symbol: str, ts: pd.Timestamp, cache: dict) -> float | None:
    if symbol not in cache:
        p = Path("data/daily") / f"{symbol}.parquet"
        cache[symbol] = pd.read_parquet(p) if p.exists() else None
    d = cache[symbol]
    if d is None or d.empty:
        return None
    day = ts.tz_convert("America/New_York").date().isoformat()
    prev = d[d["date"] < day]
    return float(prev["close"].iloc[-1]) if len(prev) else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--before", required=True)
    ap.add_argument("--per-category", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", type=Path, default=Path("reports/decision/news_events"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cost = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                            rest_mult=GAPPER_REST_MULT)
    ev = load_events(args.before)
    pc_cache: dict = {}
    ev["prior_close"] = [prior_close(s, t, pc_cache) for s, t in zip(ev.symbol, ev.ts)]
    ev = ev[ev.prior_close.between(1.0, 30.0)]
    log.info("eligible events: %d %s", len(ev), ev.cat.value_counts().to_dict())
    sample = pd.concat([g.sample(min(len(g), args.per_category), random_state=args.seed)
                        for _, g in ev.groupby("cat")], ignore_index=True)
    src = AlpacaHistoricalBars(feed="sip")
    cache_dir = Path("data/event_bars")
    cache_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for k, e in enumerate(sample.itertuples(), 1):
        key = cache_dir / f"{e.symbol}_{e.ts:%Y%m%d%H%M}.parquet"
        if key.exists():
            bars = pd.read_parquet(key)
        else:
            try:
                raw = src.fetch(e.symbol, (e.ts - timedelta(minutes=40)).to_pydatetime(),
                                (e.ts + timedelta(minutes=75)).to_pydatetime())
            except DataSourceError as exc:
                log.warning("bars failed %s %s: %s", e.symbol, e.ts, exc)
                continue
            bars = pd.DataFrame({"ts": [b.ts for b in raw], "open": [b.open for b in raw],
                                 "close": [b.close for b in raw], "volume": [b.volume for b in raw]})
            bars.to_parquet(key, index=False)
        if bars.empty:
            continue
        bars["ts"] = pd.to_datetime(bars["ts"], utc=True)
        hmin = e.ts.floor("min")
        pre = bars[bars.ts < hmin]
        hbar = bars[bars.ts == hmin]
        post = bars[bars.ts > hmin].reset_index(drop=True)
        if post.empty or (post.ts.iloc[0] - hmin) > pd.Timedelta(minutes=2):
            continue                                    # nothing tradeable right after
        base = pre.volume.tail(30).sum() / 30.0
        hv = float(hbar.volume.sum()) if len(hbar) else 0.0
        hret = float(hbar.close.iloc[0] / hbar.open.iloc[0] - 1) if len(hbar) else 0.0
        entry = float(post.open.iloc[0])
        hin = 0.5 * cost.round_trip(e.symbol, post.ts.iloc[0], entry)[0]
        rec = {"symbol": e.symbol, "ts": e.ts, "session_date": e.ts.tz_convert("America/New_York").date().isoformat(),
               "cat": e.cat, "headline": e.headline[:120], "prior_close": e.prior_close,
               "vol_ratio": hv / base if base > 0 else np.inf, "head_ret": hret,
               "reaction": bool(hv >= 5 * max(base, 1.0) and hret > 0)}
        for hz in HORIZONS:
            tgt = post[post.ts <= post.ts.iloc[0] + pd.Timedelta(minutes=hz - 1)]
            px = float(tgt.close.iloc[-1])
            hout = 0.5 * cost.round_trip(e.symbol, tgt.ts.iloc[-1], px)[0]
            rec[f"gross_{hz}"] = px / entry - 1
            rec[f"net_{hz}"] = (px * (1 - hout)) / (entry * (1 + hin)) - 1
        rows.append(rec)
        if k % 250 == 0:
            log.info("%d/%d events", k, len(sample))
    src.close()
    df = pd.DataFrame(rows)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out.with_suffix(".events.csv"), index=False)
    lines = [f"# News-catalyst event study (< {args.before}), entry at the minute after the headline", ""]
    for label, x in [("all", df), ("reaction (>=5x vol, up)", df[df.reaction]), ("no reaction", df[~df.reaction])]:
        lines += [f"## {label}", "", "| category | events | " + " | ".join(f"gross {h}m | net {h}m" for h in HORIZONS) + " | net 15m 90% CI |",
                  "|---|---|" + "---|---|" * len(HORIZONS) + "---|"]
        for cat, g in [("ALL", x)] + list(x.groupby("cat")):
            if len(g) < 20:
                continue
            ci = val.cluster_bootstrap_ci(g["net_15"].to_numpy(), g["session_date"].to_numpy(), alpha=0.10)
            cells = " | ".join(f"{100 * g[f'gross_{h}'].mean():+.2f}% | {100 * g[f'net_{h}'].mean():+.2f}%" for h in HORIZONS)
            lines.append(f"| {cat} | {len(g)} | {cells} | [{100 * ci[0]:+.2f}%, {100 * ci[1]:+.2f}%] |")
        lines.append("")
    report = "\n".join(lines) + "\n"
    args.out.with_suffix(".report.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
