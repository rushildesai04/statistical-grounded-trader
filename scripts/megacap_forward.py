"""Forward paper test of the daily per-ticker models (virtual ledger, no orders).

Rules: docs/findings/prereg_megacap_forward_2026-10-30.md.

Usage:
    python scripts/megacap_forward.py retrain [--month 2026-11]   # refresh data, fit month models
    python scripts/megacap_forward.py decide                      # 15:50 ET: write today's positions
    python scripts/megacap_forward.py evaluate                    # fill at closes, print results
    python scripts/megacap_forward.py auto                        # scheduler: retrain-if-needed / decide
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backfill_alpaca import backfill
from sgt.intraday.calendar import default_calendar
from sgt.intraday.sources import AlpacaHistoricalBars
from sgt.intraday.store import ParquetBarStore
from sgt.megacap.daily import build_daily
from sgt.megacap.live import ROOT, TICKERS, LiveError, alpaca_bars, decide, mark, model_dir, to_daily, train_month

ET = ZoneInfo("America/New_York")
CONTEXT = ("SPY", "QQQ")
ALL = list(TICKERS) + list(CONTEXT)
HIST_START = date(2016, 1, 4)
log = logging.getLogger("megacap_forward")


def adjusted_daily(end: date) -> dict[str, pd.DataFrame]:
    """Adjusted daily bars through ``end`` inclusive (Alpaca stamps daily bars 04:00 UTC, so the
    request runs one day past ``end`` and is filtered)."""
    # Free data plan: SIP may not be queried within the last 15 minutes -> exact, capped end time.
    end_ts = min(datetime.combine(end + timedelta(days=1), datetime.min.time(), ET),
                 datetime.now(ET) - timedelta(minutes=16))
    frames = alpaca_bars(ALL, "1Day", "2015-11-01", end_ts.isoformat(), feed="sip", adjustment="all")
    return {s: (d := to_daily(f))[d.index <= end] for s, f in frames.items()}


def last_session_before(d: date) -> date:
    cal, x = default_calendar(), d - timedelta(days=1)
    while not cal.is_session(x):
        x -= timedelta(days=1)
    return x


def retrain(month: str) -> int:
    if (model_dir(month) / "meta.json").exists():
        print(f"models for {month} exist")
        return 0
    first_day = pd.Period(month, "M").start_time.date()
    upto = last_session_before(min(first_day, datetime.now(ET).date()))
    store = ParquetBarStore("data/alpaca")
    stats = backfill(ALL, upto - timedelta(days=45), upto, store=store, fetch=AlpacaHistoricalBars(feed="sip").fetch)
    print("1-minute refresh:", dict(stats))
    daily = adjusted_daily(upto)
    spy_b, qqq_b = (store.read_frame(s, HIST_START, upto) for s in CONTEXT)
    feats = {}
    for s in TICKERS:
        feats[s] = build_daily(store.read_frame(s, HIST_START, upto), daily[s], spy_b, daily["SPY"], qqq_b,
                               daily["QQQ"])
        feats[s].to_parquet(ROOT / f"features_{s}_{month}.parquet")
    meta = train_month(month, feats)
    print(json.dumps({s: {k: v for k, v in m.items() if k in ("rows", "last_session")} for s, m in meta["tickers"].items()}))
    return 0


def do_decide() -> int:
    import time

    now = datetime.now(ET)
    ready = now.replace(hour=15, minute=50, second=15, microsecond=0)
    if timedelta(0) < ready - now < timedelta(minutes=6):
        time.sleep((ready - now).total_seconds())   # let the 15:49 bar publish
        now = datetime.now(ET)
    today = now.date()
    if not default_calendar().is_session(today):
        print(f"{today}: market closed")
        return 0
    start = datetime.combine(today, datetime.min.time(), ET).replace(hour=9, minute=30)
    intraday = alpaca_bars(ALL, "1Min", start.isoformat(), None, feed="iex")
    cut = pd.Timestamp(datetime.combine(today, datetime.min.time(), ET).replace(hour=15, minute=50))
    intraday = {s: f[f.ts < cut.tz_convert("UTC")] for s, f in intraday.items()}  # bars ending by 15:49
    daily = adjusted_daily(today)        # includes today's partial bar: official open (capped 16 min ago)
    path = decide(today, now, intraday, daily)
    pos = json.loads(path.read_text())["positions"]
    print(f"{today} decisions -> {path}: " + ", ".join(f"{s} {v['position']:+d}" for s, v in pos.items()))
    return 0


def evaluate() -> int:
    daily = adjusted_daily(datetime.now(ET).date())
    m = mark(daily)
    if m.empty:
        print("no filled decisions yet")
        return 0
    port = m.groupby("date").net.mean()
    cum = float((1 + port).prod() - 1)
    sharpe = float(port.mean() / port.std(ddof=1) * np.sqrt(252)) if len(port) > 2 and port.std(ddof=1) > 0 else float("nan")
    print(f"forward {port.index.min()} .. {port.index.max()} ({len(port)} days, {int((m.position != 0).sum())} trades, "
          f"{int(m.void.sum())} void rows)")
    print(f"cumulative net {cum:+.2%}  mean/day {port.mean():+.4%}  Sharpe {sharpe:+.2f}")
    print(m.groupby("symbol").net.sum().map(lambda v: f"{v:+.2%}").to_string())
    if cum < -0.05:
        print("STOP RULE HIT: cumulative net below -5%")
    m.to_csv(ROOT / "marks.csv", index=False)
    return 0


def auto() -> int:
    now = datetime.now(ET)
    month = f"{now:%Y-%m}"
    if not (model_dir(month) / "meta.json").exists():
        if now.hour >= 15:
            raise LiveError(f"no models for {month} at {now:%H:%M}; decision skipped (fail closed)")
        return retrain(month)
    if (now.hour, now.minute) >= (15, 45):
        return do_decide()
    print(f"{now:%Y-%m-%d %H:%M} ET: models ready; nothing to do")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["retrain", "decide", "evaluate", "auto"])
    ap.add_argument("--month", default=None)
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ROOT.mkdir(parents=True, exist_ok=True)
    print(f"--- {datetime.now(ET):%Y-%m-%d %H:%M:%S} ET {args.cmd}")
    if args.cmd == "retrain":
        return retrain(args.month or f"{datetime.now(ET):%Y-%m}")
    return {"decide": do_decide, "evaluate": evaluate, "auto": auto}[args.cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
