"""Forward paper test of the daily per-ticker models (virtual ledger, no orders).

Rules: docs/findings/prereg_megacap_forward_2026-10-30.md (original six, ``--set orig``, the default) and
docs/findings/prereg_megacap_ext_forward_2026-11-09.md (the other 15 names, ``--set ext``). Both sets reuse
identical model code and features (sgt.megacap.live / sgt.megacap.daily); only the ticker list and the
decisions/marks/models directory differ, so the original pre-registration is untouched.

Usage:
    python scripts/megacap_forward.py retrain [--set ext] [--month 2026-11]   # refresh data, fit month models
    python scripts/megacap_forward.py decide [--set ext]                      # 15:50 ET: write today's positions
    python scripts/megacap_forward.py evaluate [--set ext]                    # fill at closes, print results
    python scripts/megacap_forward.py catchup [--set ext]                     # decide every missed day (batch)
    python scripts/megacap_forward.py auto [--set ext]                        # scheduler entry point (= catchup)
"""
from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from backfill_alpaca import backfill
from sgt.intraday.calendar import default_calendar
from sgt.intraday.sources import AlpacaHistoricalBars
from sgt.intraday.store import ParquetBarStore
from sgt.megacap.daily import build_daily
from sgt.megacap.live import (EXT_TICKERS, ROOT, ROOT_EXT, TICKERS, LiveError, alpaca_bars, decide, mark,
                              model_dir, to_daily, train_month)

ET = ZoneInfo("America/New_York")
CONTEXT = ("SPY", "QQQ")
HIST_START = date(2016, 1, 4)


@dataclass(frozen=True)
class ForwardSet:
    """A forward test: its ticker list, its own reports directory, the first decision date, and the
    trial-ledger run id / pre-registration path. The context symbols (SPY, QQQ) are shared."""
    name: str
    tickers: tuple[str, ...]
    root: Path
    first_decision: date
    run_id: str
    kind: str
    prereg: str

    @property
    def symbols(self) -> list[str]:
        return list(self.tickers) + list(CONTEXT)


SETS = {
    "orig": ForwardSet("orig", TICKERS, ROOT, date(2026, 10, 30), "megacap-forward-2026-10-30",
                       "megacap-forward", "docs/findings/prereg_megacap_forward_2026-10-30.md"),
    "ext": ForwardSet("ext", EXT_TICKERS, ROOT_EXT, date(2026, 11, 9), "megacap-ext-forward-2026-11-09",
                      "megacap-ext-forward", "docs/findings/prereg_megacap_ext_forward_2026-11-09.md"),
}
FIRST_DECISION = SETS["orig"].first_decision    # back-compat alias (prereg amendment A2)
log = logging.getLogger("megacap_forward")


def adjusted_daily(end: date, cfg: ForwardSet) -> dict[str, pd.DataFrame]:
    """Adjusted daily bars through ``end`` inclusive (Alpaca stamps daily bars 04:00 UTC, so the
    request runs one day past ``end`` and is filtered)."""
    # Free data plan: SIP may not be queried within the last 15 minutes -> exact, capped end time.
    end_ts = min(datetime.combine(end + timedelta(days=1), datetime.min.time(), ET),
                 datetime.now(ET) - timedelta(minutes=16))
    frames = alpaca_bars(cfg.symbols, "1Day", "2015-11-01", end_ts.isoformat(), feed="sip", adjustment="all")
    return {s: (d := to_daily(f))[d.index <= end] for s, f in frames.items()}


def last_session_before(d: date) -> date:
    cal, x = default_calendar(), d - timedelta(days=1)
    while not cal.is_session(x):
        x -= timedelta(days=1)
    return x


MIN_HISTORY = 0.95          # share of sessions since a ticker's first stored session that must be present


def check_history(store: ParquetBarStore, cfg: ForwardSet, upto: date) -> None:
    """Fail closed before training on a thin store (2026-11-09: the runner held 1-minute history only for the
    original six; the 45-day refresh above would otherwise train the extended set on ~1 month of data)."""
    cal = default_calendar()
    for s in cfg.symbols:
        have = sorted(d for d in store.sessions(s) if d <= upto)
        if not have or have[0] > HIST_START + timedelta(days=3 * 365):
            listed = have[0] if have else None
            if s not in ("RDDT",) or not have:                 # RDDT listed 2024-03-21: its history starts there
                raise LiveError(f"{s}: 1-minute history starts {listed}; backfill from {HIST_START} first")
        span = [have[0] + timedelta(days=i) for i in range((upto - have[0]).days + 1)]
        want = sum(cal.is_session(d) for d in span)
        if len(have) < MIN_HISTORY * want:
            raise LiveError(f"{s}: {len(have)} of {want} sessions stored; backfill before retraining")


def retrain(month: str, cfg: ForwardSet) -> int:
    if (model_dir(month, cfg.root) / "meta.json").exists():
        print(f"models for {month} exist")
        return 0
    first_day = pd.Period(month, "M").start_time.date()
    upto = last_session_before(min(first_day, datetime.now(ET).date()))
    store = ParquetBarStore("data/alpaca")
    stats = backfill(cfg.symbols, upto - timedelta(days=45), upto, store=store,
                     fetch=AlpacaHistoricalBars(feed="sip").fetch)
    print("1-minute refresh:", dict(stats))
    check_history(store, cfg, upto)
    daily = adjusted_daily(upto, cfg)
    spy_b, qqq_b = (store.read_frame(s, HIST_START, upto) for s in CONTEXT)
    feats = {}
    for s in cfg.tickers:
        feats[s] = build_daily(store.read_frame(s, HIST_START, upto), daily[s], spy_b, daily["SPY"], qqq_b,
                               daily["QQQ"])
        feats[s].to_parquet(cfg.root / f"features_{s}_{month}.parquet")
    meta = train_month(month, feats, root=cfg.root)
    print(json.dumps({s: {k: v for k, v in m.items() if k in ("rows", "last_session")} for s, m in meta["tickers"].items()}))
    return 0


def do_decide(cfg: ForwardSet) -> int:
    import time

    now = datetime.now(ET)
    ready = now.replace(hour=15, minute=50, second=15, microsecond=0)
    if timedelta(0) < ready - now < timedelta(minutes=6):
        time.sleep((ready - now).total_seconds())   # let the 15:49 bar publish
        now = datetime.now(ET)
    today = now.date()
    if today < cfg.first_decision:
        raise LiveError(f"forward test starts {cfg.first_decision}")
    if not default_calendar().is_session(today):
        print(f"{today}: market closed")
        return 0
    start = datetime.combine(today, datetime.min.time(), ET).replace(hour=9, minute=30)
    intraday = alpaca_bars(cfg.symbols, "1Min", start.isoformat(), None, feed="iex")
    cut = pd.Timestamp(datetime.combine(today, datetime.min.time(), ET).replace(hour=15, minute=50))
    intraday = {s: f[f.ts < cut.tz_convert("UTC")] for s, f in intraday.items()}  # bars ending by 15:49
    daily = adjusted_daily(today, cfg)   # includes today's partial bar: official open (capped 16 min ago)
    # write-time stamp (adversarial pass 4, F-14): the fetches above can take minutes
    path = decide(today, now, intraday, daily, tickers=cfg.tickers, root=cfg.root, clock=lambda: datetime.now(ET))
    pos = json.loads(path.read_text())["positions"]
    print(f"{today} decisions -> {path}: " + ", ".join(f"{s} {v['position']:+d}" for s, v in pos.items()))
    return 0


def evaluate(cfg: ForwardSet) -> int:
    daily = adjusted_daily(datetime.now(ET).date(), cfg)
    m = mark(daily, root=cfg.root)
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
    m.to_csv(cfg.root / "marks.csv", index=False)
    record_months(port, datetime.now(ET).date(), cfg)
    return 0


def month_summaries(port: pd.Series, today: date) -> dict[str, dict]:
    """Cumulative forward metrics at the end of every calendar month that is over (prereg: each
    monthly evaluation goes to the trial ledger)."""
    out = {}
    idx = pd.to_datetime(pd.Series(port.index))
    for month in sorted({f"{d:%Y-%m}" for d in idx}):
        if pd.Period(month, "M").end_time.date() >= today:
            continue
        p = port[(idx <= pd.Period(month, "M").end_time).to_numpy()]
        sd = p.std(ddof=1) if len(p) > 2 else float("nan")
        out[month] = {"through": str(max(p.index)), "days": int(len(p)), "cum_net": float((1 + p).prod() - 1),
                      "mean_daily": float(p.mean()),
                      "sharpe": float(p.mean() / sd * np.sqrt(252)) if sd and sd > 0 else None}
    return out


def record_months(port: pd.Series, today: date, cfg: ForwardSet) -> None:
    from sgt.db.session import get_session, init_db
    from sgt.decision import trial_ledger

    months = month_summaries(port, today)
    if not months:
        return
    init_db()
    with get_session() as s:
        for month, metrics in months.items():   # idempotent on (run_id, params)
            trial_ledger.record_trial(s, run_id=cfg.run_id, kind=cfg.kind,
                                      params={"prereg": cfg.prereg, "month": month}, metrics=metrics)
    print("trial ledger: recorded months", ", ".join(months))


def last_complete_session(now: datetime) -> date:
    """Latest session the 1-minute store will accept: backfill writes a session only after its
    extended-hours close (post_close, 20:00 ET; 17:00 on early closes) and SIP must be >15 min old.
    (2026-10-30: was 16:10 ET, so evening runs found no bars for today and failed closed.)"""
    cal = default_calendar()
    today = now.date()
    if cal.is_session(today) and now >= cal.bounds(today).post_close + timedelta(minutes=16):
        return today
    return last_session_before(today)


def catchup(cfg: ForwardSet) -> int:
    """Batch-decide every session from the set's first decision through the last complete one that has no
    decision yet (amendment A1): laptop-friendly, identical to a 15:50 decision by construction."""
    now = datetime.now(ET)
    end = last_complete_session(now)
    cal = default_calendar()
    days, d = [], cfg.first_decision
    while d <= end:
        if cal.is_session(d) and not (cfg.root / "decisions" / f"{d}.json").exists():
            days.append(d)
        d += timedelta(days=1)
    if not days:
        print(f"up to date through {end}")
        return evaluate(cfg)
    for month in sorted({f"{x:%Y-%m}" for x in days}):
        retrain(month, cfg)
    store = ParquetBarStore("data/alpaca")
    stats = backfill(cfg.symbols, days[0], days[-1], store=store, fetch=AlpacaHistoricalBars(feed="sip").fetch)
    print("1-minute refresh:", dict(stats))
    daily = adjusted_daily(end, cfg)
    for x in days:
        intr = {}
        for s in cfg.symbols:
            f = store.read_frame(s, x, x)
            f["ts"] = pd.to_datetime(f.ts, utc=True)
            cut = pd.Timestamp(datetime.combine(x, datetime.min.time(), ET).replace(hour=15, minute=50))
            intr[s] = f[f.ts < cut.tz_convert("UTC")][["ts", "open", "high", "low", "close", "volume"]]
        path = decide(x, now, intr, {s: v[v.index <= x] for s, v in daily.items()}, mode="batch",
                      tickers=cfg.tickers, root=cfg.root, clock=lambda: datetime.now(ET))
        pos = json.loads(path.read_text())["positions"]
        print(f"{x} batch decisions: " + ", ".join(f"{s} {v['position']:+d}" for s, v in pos.items()))
    return evaluate(cfg)


def auto(cfg: ForwardSet) -> int:
    now = datetime.now(ET)
    if now.date() < cfg.first_decision:
        print(f"forward test starts {cfg.first_decision}; nothing to do")
        return 0
    return catchup(cfg)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["retrain", "decide", "evaluate", "catchup", "auto"])
    ap.add_argument("--set", dest="set_name", choices=sorted(SETS), default="orig",
                    help="forward test to run: 'orig' (six names, default) or 'ext' (the other 15)")
    ap.add_argument("--month", default=None)
    args = ap.parse_args()
    cfg = SETS[args.set_name]
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    cfg.root.mkdir(parents=True, exist_ok=True)
    print(f"--- {datetime.now(ET):%Y-%m-%d %H:%M:%S} ET {args.cmd} [{cfg.name}]")
    if args.cmd == "retrain":
        return retrain(args.month or f"{datetime.now(ET):%Y-%m}", cfg)
    return {"decide": do_decide, "evaluate": evaluate, "catchup": catchup, "auto": auto}[args.cmd](cfg)


if __name__ == "__main__":
    raise SystemExit(main())
