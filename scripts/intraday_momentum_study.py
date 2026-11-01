"""Market intraday momentum (Gao, Han, Li & Zhou 2018, JFE), long-only, pre-registered.

Rule (fixed before looking): r_first = price at 10:00 ET / previous RTH close - 1. If
r_first > 0, buy at 15:30 ET and sell at the 16:00 close; otherwise stay flat. Variant
(also from the paper): require the 12:00->15:30 return to agree in sign (r_mid > 0).
Costs: 1 bp per side (index ETFs, ~1-cent spread on $400+). Training period vs the
held-out period are reported separately; days clustered by nothing (one trade per day).

Usage:
    python scripts/intraday_momentum_study.py --symbols SPY,QQQ --start 2016-01-04 --split 2025-10-01
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.intraday.sources import AlpacaHistoricalBars

ET = "America/New_York"


def load(symbol: str, start: date, end: date, cache: Path) -> pd.DataFrame:
    p = cache / f"{symbol}_{start}_{end}.parquet"
    if p.exists():
        return pd.read_parquet(p)
    src = AlpacaHistoricalBars(feed="sip")
    parts = []
    y = start.year
    while y <= end.year:
        a = max(start, date(y, 1, 1)); b = min(end, date(y, 12, 31))
        for m0 in (1, 4, 7, 10):
            qa = max(a, date(y, m0, 1)); qb = min(b, date(y + (m0 == 10), (m0 + 3 - 1) % 12 + 1, 1))
            if qa >= qb:
                continue
            bars = src.fetch(symbol, datetime.combine(qa, dtime.min, tzinfo=timezone.utc),
                             datetime.combine(qb, dtime.min, tzinfo=timezone.utc))
            parts.append(pd.DataFrame({"ts": [x.ts for x in bars], "close": [x.close for x in bars],
                                       "seg": [x.segment.value for x in bars]}))
        y += 1
    src.close()
    df = pd.concat(parts, ignore_index=True).drop_duplicates("ts")
    df.to_parquet(p, index=False)
    return df


def daily_signals(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df.seg == "rth"].copy()
    df["et"] = pd.to_datetime(df.ts, utc=True).dt.tz_convert(ET)
    df["d"] = df.et.dt.date
    df["hm"] = df.et.dt.hour * 100 + df.et.dt.minute
    rows = []
    prev_close = None
    for d, g in df.groupby("d"):
        g = g.sort_values("et")
        def at(hm):
            x = g[g.hm <= hm]
            return float(x.close.iloc[-1]) if len(x) else np.nan
        last = float(g.close.iloc[-1])
        p10, p12, p1530 = at(959), at(1159), at(1529)
        if prev_close is not None and g.hm.max() >= 1555:
            rows.append({"d": d, "r_first": p10 / prev_close - 1, "r_mid": p1530 / p12 - 1,
                         "r_last": last / p1530 - 1})
        prev_close = last
    return pd.DataFrame(rows)


def stats(r: pd.Series) -> str:
    if r.empty:
        return "n=0"
    t = r.mean() / r.std(ddof=1) * np.sqrt(len(r)) if len(r) > 2 else np.nan
    ann = r.mean() * 252 * (len(r) / max(1, len(r)))  # per-traded-day mean scaled
    return (f"n={len(r)} mean={1e4 * r.mean():+.2f}bp hit={(r > 0).mean():.0%} t={t:+.2f} "
            f"sum={100 * r.sum():+.1f}%")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", default="SPY,QQQ")
    ap.add_argument("--start", type=date.fromisoformat, default=date(2016, 1, 4))
    ap.add_argument("--end", type=date.fromisoformat, default=date(2026, 10, 16))
    ap.add_argument("--split", type=date.fromisoformat, default=date(2025, 10, 1))
    ap.add_argument("--cost-bp", type=float, default=1.0)
    args = ap.parse_args(argv)
    cache = Path("data/etf_1m"); cache.mkdir(parents=True, exist_ok=True)
    for sym in args.symbols.split(","):
        s = daily_signals(load(sym, args.start, args.end, cache))
        cost = 2 * args.cost_bp / 1e4
        s["trade"] = s.r_last - cost
        for label, part in (("TRAIN", s[s.d < args.split]), ("HELD-OUT", s[s.d >= args.split])):
            print(f"{sym} {label} ({len(part)} days)")
            print("   always long last 30m :", stats(part.trade))
            print("   rule r_first>0        :", stats(part.trade[part.r_first > 0]))
            print("   rule r_first>0&r_mid>0:", stats(part.trade[(part.r_first > 0) & (part.r_mid > 0)]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
