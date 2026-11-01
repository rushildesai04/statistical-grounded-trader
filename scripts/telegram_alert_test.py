"""Score the pre-registered Telegram alert test ONCE.

Rules: docs/findings/prereg_telegram_alerts_2026-10-29.md (incl. amendments A1, A2).
Held-out results are computed only for cells that pass training.

Usage:
    python scripts/telegram_alert_test.py --channel=-1001254273411
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.decision.features import CostModelAdapter
from sgt.intraday.calendar import default_calendar
from sgt.intraday.store import ParquetBarStore

ET = "America/New_York"
SEED = 20261029
N_BOOT = 10_000
REACTION = pd.Timedelta(seconds=10)
MAX_ENTRY_DELAY = pd.Timedelta(minutes=5)
PRIMARY_H = 15
SECONDARY_H = (5, 60)
CELL_OF = {  # alert_type -> cell (A1); None = not a cell
    "volume_increasing": "volume_increasing", "volume_spike": "volume_spike",
    "gaining_volume": "gaining_volume", "gaining_momentum": "gaining_momentum",
    "new_high": "high_of_day", "intraday_high": "high_of_day", "testing_high": "testing_high",
    "price_level": "price_level", "resumed": "resumed", "news": "news",
    "premarket_high": None, "after_hours_high": None, "halted": None,
}
EXT_TYPES = ("premarket_high", "after_hours_high")
OUT = Path("reports/telegram")


def load_alerts(db: str, channel: str) -> pd.DataFrame:
    with sqlite3.connect(db) as c:
        df = pd.read_sql(
            "SELECT a.symbol, a.alert_type, a.posted_at, a.has_news, m.edited_at "
            "FROM crowd_alerts a JOIN telegram_messages m USING (channel, message_id) "
            "WHERE a.channel = ?", c, params=(channel,))
    n_all = len(df)
    df = df[df.edited_at.isna()].drop(columns="edited_at")
    n_edit = n_all - len(df)
    df = df[df.alert_type != "halted"]
    df = df[(df.alert_type != "news") | (df.has_news == 1)]
    df["posted_at"] = pd.to_datetime(df.posted_at, utc=True, format="ISO8601")
    df["session"] = df.posted_at.dt.tz_convert(ET).dt.date
    cal = default_calendar()
    df = df[df.session.map(cal.is_session)]
    # forwarded/quoted duplicates: same symbol + type within 60 s
    df = df.sort_values("posted_at")
    gap = df.groupby(["symbol", "alert_type"]).posted_at.diff()
    n_before = len(df)
    df = df[~(gap <= pd.Timedelta(seconds=60))]
    print(f"alerts: {n_all} parsed, {n_edit} edited excluded, {n_before - len(df)} 60s duplicates dropped, "
          f"{len(df)} kept")
    return df.reset_index(drop=True)


def attach_trades(df: pd.DataFrame, store: ParquetBarStore) -> pd.DataFrame:
    cost = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                            rest_mult=GAPPER_REST_MULT)
    cache: dict[tuple[str, date], pd.DataFrame | None] = {}
    rows = []
    for r in df.itertuples(index=False):
        key = (r.symbol, r.session)
        if key not in cache:
            bars = store.read(r.symbol, r.session, r.session)
            cache[key] = None if not bars else pd.DataFrame(
                {"ts": pd.to_datetime([b.ts for b in bars], utc=True),
                 "open": [b.open for b in bars], "close": [b.close for b in bars]})
        b = cache[key]
        out = {"status": "no_bars"}
        if b is not None:
            i = int(b.ts.searchsorted(r.posted_at + REACTION, side="left"))
            if i >= len(b) or b.ts.iloc[i] - r.posted_at > MAX_ENTRY_DELAY:
                out = {"status": "no_entry"}
            else:
                t0, p0 = b.ts.iloc[i], float(b.open.iloc[i])
                out = {"status": "ok", "entry_ts": t0, "entry": p0}
                for h in (PRIMARY_H, *SECONDARY_H):
                    j = int(b.ts.searchsorted(t0 + pd.Timedelta(minutes=h), side="left"))
                    px = float(b.open.iloc[j]) if j < len(b) else float(b.close.iloc[-1])
                    out[f"gross_{h}"] = px / p0 - 1.0 if p0 > 0 else np.nan
                k = int(b.ts.searchsorted(r.posted_at - pd.Timedelta(minutes=15), side="right")) - 1
                out["latency"] = p0 / float(b.close.iloc[k]) - 1.0 if 0 <= k < i else np.nan
                out["cost_rt"] = cost.round_trip(r.symbol, t0.to_pydatetime(), p0)[0] if p0 > 0 else np.nan
        rows.append(out)
    trade_cols = ["status", "entry_ts", "entry", *(f"gross_{h}" for h in (PRIMARY_H, *SECONDARY_H)), "latency", "cost_rt"]
    t = pd.concat([df, pd.DataFrame(rows, index=df.index).reindex(columns=trade_cols)], axis=1)
    t["entry_ts"] = pd.to_datetime(t.entry_ts, utc=True)
    et = t.entry_ts.dt.tz_convert(ET)
    mins = et.dt.hour * 60 + et.dt.minute
    t["primary_window"] = (mins >= 9 * 60 + 30) & (mins <= 15 * 60 + 44)
    t["window"] = np.select([mins < 9 * 60 + 30, mins < 10 * 60 + 30, mins < 15 * 60, mins < 16 * 60],
                            ["pre", "open", "midday", "power_hour"], "post")
    for h in (PRIMARY_H, *SECONDARY_H):
        t[f"net_{h}"] = t[f"gross_{h}"] - t.cost_rt
    t["net_15_x1_5"] = t.gross_15 - 1.5 * t.cost_rt
    return t


def primary_events(t: pd.DataFrame) -> pd.DataFrame:
    """Primary population: cell alerts with an entry inside 09:30-15:44 ET priced $1-50, by time.
    (Shared with the alert-fade forward record, so both use identical events.)"""
    cand = t[t.alert_type.map(CELL_OF).notna()].assign(cell=lambda d: d.alert_type.map(CELL_OF))
    ok = (cand.status == "ok") & cand.primary_window & cand.entry.between(1, 50)
    return cand[ok].sort_values("posted_at")


def boot_means(x: np.ndarray, days: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    ud, inv = np.unique(days, return_inverse=True)
    s = np.bincount(inv, weights=x)
    n = np.bincount(inv).astype(float)
    idx = rng.integers(0, len(ud), size=(N_BOOT, len(ud)))
    return s[idx].sum(1) / n[idx].sum(1)


def top5_share(x: np.ndarray) -> float:
    tot = x.sum()
    return float(np.sort(x)[-5:].sum() / tot) if tot > 0 else float("inf")


def stats(g: pd.DataFrame, col: str = "net_15") -> dict:
    x = g[col].to_numpy(float)
    return {"n": len(x), "mean": float(x.mean()) if len(x) else np.nan,
            "median": float(np.median(x)) if len(x) else np.nan,
            "hit": float((x > 0).mean()) if len(x) else np.nan, "days": int(g.session.nunique())}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--channel", required=True)
    ap.add_argument("--db", default="sgt.db")
    ap.add_argument("--data", default="data/alpaca")
    ap.add_argument("--dry-run", action="store_true", help="counts and data coverage only; no returns")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    alerts = load_alerts(args.db, args.channel)
    t = attach_trades(alerts, ParquetBarStore(args.data))
    t["cell"] = t.alert_type.map(CELL_OF)

    # ---- primary population (A2: filter, then first per type per symbol-session)
    cand = t[t.cell.notna()].copy()
    # data guard denominator: alerts posted inside the window whose trade could not be built
    et = cand.posted_at.dt.tz_convert(ET)
    pm = et.dt.hour * 60 + et.dt.minute
    posted_in_win = (pm >= 9 * 60 + 30) & (pm <= 15 * 60 + 43)
    unusable = posted_in_win & (cand.status != "ok")
    guard = float(unusable.sum() / max(posted_in_win.sum(), 1))
    by_status = cand[posted_in_win].status.value_counts().to_dict()

    prim = primary_events(t)
    cells = {c: g.drop_duplicates(["symbol", "session"]) for c, g in prim.groupby("cell")}
    cells["pooled_first"] = prim.drop_duplicates(["symbol", "session"])
    union = pd.concat([g for c, g in cells.items() if c != "pooled_first"])
    d_star = pd.Series(sorted(union.posted_at)).quantile(0.75, interpolation="lower")
    d_star = d_star.tz_convert(ET).date()
    print(f"D* = {d_star}  (training < D*, held-out >= D*)  data guard: {guard:.1%} unusable {by_status}")
    if args.dry_run:
        for c, g in cells.items():
            print(f"{c:20s} train n={int((g.session < d_star).sum()):5d}  holdout n={int((g.session >= d_star).sum()):5d}")
        return 0

    train = {c: g[g.session < d_star] for c, g in cells.items()}
    hold = {c: g[g.session >= d_star] for c, g in cells.items()}
    k = sum(len(g) >= 100 for g in train.values())
    alpha = 0.05 / max(k, 1)
    rng = np.random.default_rng(SEED)

    results = {}
    print(f"\nPRIMARY (15 min, net of costs) k={k}, one-sided alpha={alpha:.4f}")
    print(f"{'cell':20s} {'n':>5s} {'mean':>8s} {'median':>8s} {'hit':>5s} {'lower':>8s} {'top5':>6s}  training verdict")
    for c in sorted(cells, key=lambda c: -len(train[c])):
        g = train[c]
        s = stats(g)
        if s["n"] >= 2:
            lb = float(np.quantile(boot_means(g.net_15.to_numpy(float), g.session.astype(str).to_numpy(), rng), alpha))
        else:
            lb = np.nan
        t5 = top5_share(g.net_15.to_numpy(float))
        passed = s["n"] >= 100 and lb > 0 and t5 < 0.5
        results[c] = {"train": {**s, "lower": lb, "top5_share": t5}, "train_pass": passed}
        print(f"{c:20s} {s['n']:5d} {s['mean']:8.2%} {s['median']:8.2%} {s['hit']:5.0%} {lb:8.2%} "
              f"{t5:6.2f}  {'PASS' if passed else 'fail'}")

    print("\nHELD-OUT (only cells that passed training)")
    verdicts = {}
    for c, r in results.items():
        if not r["train_pass"]:
            verdicts[c] = "FAIL"
            continue
        g = hold[c]
        s = stats(g)
        t5 = top5_share(g.net_15.to_numpy(float))
        r["holdout"] = {**s, "top5_share": t5}
        if s["n"] < 30:
            verdicts[c] = "INCONCLUSIVE"
        else:
            verdicts[c] = "PASS" if (s["mean"] > 0 and t5 < 0.5) else "FAIL"
        print(f"{c:20s} n={s['n']} mean={s['mean']:.2%} hit={s['hit']:.0%} top5={t5:.2f} -> {verdicts[c]}")
    if not any(r["train_pass"] for r in results.values()):
        print("(none passed training; held-out not examined)")
    overall = ("INCONCLUSIVE (data)" if guard > 0.20 else
               "PASS" if "PASS" in verdicts.values() else
               "INCONCLUSIVE" if "INCONCLUSIVE" in verdicts.values() else "FAIL")
    print(f"\nVERDICT: {overall}   per cell: {verdicts}")

    # ---- secondary, training only
    tr_all = pd.concat([g.assign(cell=c) for c, g in train.items()])
    print("\nSECONDARY (training only; not used for the decision)")
    sec = tr_all.groupby("cell").agg(n=("net_15", "size"), gross_5=("gross_5", "mean"), gross_15=("gross_15", "mean"),
                                     gross_60=("gross_60", "mean"), net_5=("net_5", "mean"), net_15=("net_15", "mean"),
                                     net_60=("net_60", "mean"), net_15_x1_5=("net_15_x1_5", "mean"),
                                     cost=("cost_rt", "mean"), latency=("latency", "median"))
    print(sec.to_string(float_format=lambda v: f"{v:.2%}"))
    pooled_tr = train["pooled_first"]
    print("\npooled_first by session window:")
    print(pooled_tr.groupby("window").agg(n=("net_15", "size"), gross=("gross_15", "mean"), net=("net_15", "mean"),
                                          hit=("net_15", lambda x: (x > 0).mean())).to_string(float_format=lambda v: f"{v:.2%}"))
    bucket = pd.cut(pooled_tr.entry, [1, 5, 20, 50], labels=["$1-5", "$5-20", "$20-50"], include_lowest=True)
    print("\npooled_first by price bucket:")
    print(pooled_tr.groupby(bucket, observed=True).agg(n=("net_15", "size"), gross=("gross_15", "mean"),
                                                       net=("net_15", "mean")).to_string(float_format=lambda v: f"{v:.2%}"))
    ext = t[t.alert_type.isin(EXT_TYPES + tuple(k2 for k2, v in CELL_OF.items() if v)) & (t.status == "ok")
            & ~t.primary_window & t.entry.between(1, 50)].sort_values("posted_at")
    ext = ext[ext.session < d_star].drop_duplicates(["alert_type", "symbol", "session"])
    print("\nextended hours (training, first per type per symbol-session):")
    print(ext.groupby("alert_type").agg(n=("net_15", "size"), gross=("gross_15", "mean"), net=("net_15", "mean"))
          .to_string(float_format=lambda v: f"{v:.2%}"))

    for c, g in cells.items():
        g.assign(cell=c, split=np.where(g.session < d_star, "train", "holdout")).to_csv(OUT / f"trades_{c}.csv", index=False)
    summary = {"d_star": str(d_star), "k": k, "alpha": alpha, "data_guard_unusable": guard, "verdict": overall,
               "verdicts": verdicts, "cells": results}
    (OUT / "result.json").write_text(json.dumps(summary, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="telegram-alerts-2026-10-29", kind="telegram-alerts",
                                  params={"prereg": "docs/findings/prereg_telegram_alerts_2026-10-29.md"},
                                  metrics=json.loads(json.dumps(summary, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
