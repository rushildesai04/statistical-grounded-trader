"""H3 opening-range-breakout study on in-play gap days (sgt.decision.orb).

Protocol (no lockbox snooping):
1. evaluate a FIXED, small variant grid on TRAINING sessions only (< --lockbox-start);
   every variant is recorded in the trial ledger (kind="orb");
2. pick the variant with the best training lower-CI expectancy (cluster bootstrap by day);
3. score ONLY that variant on the lockbox, once, and record the look (kind="orb-lockbox").

Usage:
    python scripts/orb_study.py --gappers data/universe/gappers.csv --lockbox-start 2026-10-05
"""
from __future__ import annotations

import argparse
import csv
import logging
from collections import defaultdict
from dataclasses import asdict
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.decision import validation as val
from sgt.decision.features import CostModelAdapter
from sgt.decision.orb import ORBConfig, orb_trade
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger("orb_study")

VARIANTS: tuple[ORBConfig, ...] = (
    ORBConfig(),
    ORBConfig(min_open_rel_vol=2.0),
    ORBConfig(min_open_rel_vol=5.0),
    ORBConfig(price_min=5.0),
    ORBConfig(price_min=5.0, min_open_rel_vol=2.0),
    ORBConfig(or_minutes=15),
)


def load_days(path: Path, min_prev_volume: float) -> dict[str, list[date]]:
    out: dict[str, list[date]] = defaultdict(list)
    with path.open() as fh:
        for r in csv.DictReader(fh):
            if float(r["prev_volume"]) >= min_prev_volume:
                out[r["symbol"].upper()].append(date.fromisoformat(r["session"]))
    return out


def run_variants(cfgs, days, store, cost, lookback=14) -> list[pd.DataFrame]:
    """One pass over the store: every variant is evaluated on each symbol's bars."""
    rows: list[list[dict]] = [[] for _ in cfgs]
    for sym, ds in days.items():
        ds = [d for d in ds]
        sessions = store.sessions(sym)
        if not ds or not sessions:
            continue
        bars = store.read(sym, sessions[0], max(ds))
        by_day: dict[date, list] = defaultdict(list)
        for b in bars:
            by_day[b.session_date].append(b)
        order = sorted(by_day)
        for d in ds:
            if d not in by_day:
                continue
            i = order.index(d)
            prior = [by_day[x] for x in order[max(0, i - lookback):i]]
            for k, cfg in enumerate(cfgs):
                t = orb_trade(by_day[d], prior, cfg, cost)
                if t is not None:
                    rows[k].append(asdict(t))
    return [pd.DataFrame(r) for r in rows]


def run_variant(cfg, days, store, cost, lookback=14) -> pd.DataFrame:
    return run_variants([cfg], days, store, cost, lookback)[0]


def summarize(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"n": 0, "mean": float("nan"), "ci": (float("nan"), float("nan")),
                "hit": float("nan"), "gross": float("nan"), "days": 0}
    clusters = (df["session"].astype(str)).to_numpy()   # same-day trades are correlated
    lo, hi = val.cluster_bootstrap_ci(df["net_return"].to_numpy(), clusters, alpha=0.10)
    return {"n": len(df), "mean": float(df["net_return"].mean()), "ci": (lo, hi),
            "hit": float((df["net_return"] > 0).mean()), "gross": float(df["gross_return"].mean()),
            "days": int(df["session"].nunique())}


def _fmt(name, s) -> str:
    return (f"| {name} | {s['n']} | {s['days']} | {100 * s['gross']:+.3f}% | {100 * s['mean']:+.3f}% | "
            f"[{100 * s['ci'][0]:+.3f}%, {100 * s['ci'][1]:+.3f}%] | {s['hit']:.1%} |")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--gappers", type=Path, default=Path("data/universe/gappers.csv"))
    ap.add_argument("--min-prev-volume", type=float, default=50_000)
    ap.add_argument("--lockbox-start", required=True, type=date.fromisoformat)
    ap.add_argument("--data", default="data/alpaca")
    ap.add_argument("--run-id", default="orb-v1")
    ap.add_argument("--out", type=Path, default=Path("reports/decision/orb_v1"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    store = ParquetBarStore(args.data)
    cost = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                            rest_mult=GAPPER_REST_MULT)
    all_days = load_days(args.gappers, args.min_prev_volume)
    train_days = {s: [d for d in ds if d < args.lockbox_start] for s, ds in all_days.items()}
    lock_days = {s: [d for d in ds if d >= args.lockbox_start] for s, ds in all_days.items()}
    init_db()

    header = ["| variant | trades | days | gross | net | 90% CI (day-clustered) | hit |",
              "|---|---|---|---|---|---|---|"]
    lines = ["# H3 ORB study", "", f"Training sessions < {args.lockbox_start}", ""] + header
    best = None
    train_results = run_variants(VARIANTS, train_days, store, cost)
    for k, cfg in enumerate(VARIANTS):
        s = summarize(train_results[k])
        lines.append(_fmt(f"v{k} {cfg}", s))
        log.info("train v%d: n=%d mean=%.4f ci=%s", k, s["n"], s["mean"], s["ci"])
        with get_session() as session:
            trial_ledger.record_trial(session, run_id=args.run_id, kind="orb",
                                      params={"variant": k, **asdict(cfg)},
                                      metrics={"n": s["n"], "mean": s["mean"], "ci_lo": s["ci"][0]})
        # Only a variant whose TRAINING lower bound is above zero earns a lockbox look; a
        # strategy that lost in training is rejected, not "tested anyway" (2026-10-18).
        if s["n"] >= 30 and s["ci"][0] > 0 and (best is None or s["ci"][0] > best[1]["ci"][0]):
            best = (k, s, cfg)

    if best is None:
        lines += ["", "No variant had >= 30 training trades with a positive lower CI; "
                      "lockbox not opened."]
    else:
        k, s_train, cfg = best
        lb = run_variant(cfg, lock_days, store, cost)
        s = summarize(lb)
        lines += ["", f"Selected on training: v{k} (lower CI {100 * s_train['ci'][0]:+.3f}%)",
                  "", f"## Lockbox (sessions >= {args.lockbox_start}), scored once", ""] + header
        lines.append(_fmt(f"v{k}", s))
        if not lb.empty:
            # $ simulation: risk 0.5% of equity per trade on the stop distance, 6% total cap
            eq = 100_000.0
            risk = (lb["entry_price"] - lb["stop"]) / lb["entry_price"]
            frac = np.minimum(0.005 / risk.clip(lower=1e-4), 0.25)
            lb["pnl_usd"] = frac * eq * lb["net_return"]
            daily = lb.groupby(lb["session"].astype(str))["pnl_usd"].sum()
            lines += ["", f"$ simulation on ${eq:,.0f} (0.5% risk/trade, <=25% notional/trade): "
                      f"P&L ${lb['pnl_usd'].sum():+,.2f}", "", "| session | trades | P&L |", "|---|---|---|"]
            cnt = lb.groupby(lb["session"].astype(str)).size()
            lines += [f"| {d} | {cnt[d]} | ${v:+,.2f} |" for d, v in daily.items()]
            args.out.parent.mkdir(parents=True, exist_ok=True)
            lb.to_csv(args.out.with_suffix(".lockbox_trades.csv"), index=False)
        with get_session() as session:
            trial_ledger.record_trial(session, run_id=args.run_id, kind="orb-lockbox",
                                      params={"variant": k}, metrics={"n": s["n"], "mean": s["mean"]})
    report = "\n".join(lines) + "\n"
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.with_suffix(".report.md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
