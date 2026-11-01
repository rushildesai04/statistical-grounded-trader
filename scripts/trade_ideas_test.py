"""Score the pre-registered trade-ideas stock-call test ONCE.

Rules: docs/findings/prereg_trade_ideas_2026-10-29.md. Sizing/limits come from the live bot's
own ``sgt.signals_live.rules.decide_idea`` so backtest and bot cannot disagree.

Usage:
    python scripts/trade_ideas_test.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import time
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.decision.features import CostModelAdapter
from sgt.intraday.calendar import default_calendar
from sgt.intraday.store import ParquetBarStore
from sgt.signals_live.ideas import StockIdea, parse_idea
from sgt.signals_live.rules import IdeaPlan, Limits, State, decide_idea
from telegram_alert_test import boot_means, top5_share

ET = "America/New_York"
CHANNEL = "-1001204455879"
SEED = 20261029
OUT = Path("reports/trade_ideas")
COST = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                        rest_mult=GAPPER_REST_MULT)


def load_calls(db: str) -> pd.DataFrame:
    with sqlite3.connect(db) as c:
        df = pd.read_sql("SELECT message_id, posted_at, raw_text FROM telegram_messages "
                         "WHERE channel = ? AND edited_at IS NULL", c, params=(CHANNEL,))
    df["idea"] = df.raw_text.map(parse_idea)
    df = df[df.idea.map(lambda x: isinstance(x, StockIdea))].copy()
    df["symbol"] = df.idea.map(lambda x: x.symbol)
    df["posted_at"] = pd.to_datetime(df.posted_at, utc=True, format="ISO8601")
    df["session"] = df.posted_at.dt.tz_convert(ET).dt.date
    cal = default_calendar()
    df = df[df.session.map(cal.is_session)].sort_values("posted_at")
    return df.drop_duplicates(["symbol", "session"]).reset_index(drop=True)


def _frame(bars) -> pd.DataFrame:
    f = pd.DataFrame({"ts": pd.to_datetime([b.ts for b in bars], utc=True),
                      "open": [b.open for b in bars], "high": [b.high for b in bars],
                      "low": [b.low for b in bars], "close": [b.close for b in bars]})
    et = f.ts.dt.tz_convert(ET)
    f["tod"] = et.dt.hour * 60 + et.dt.minute
    return f


def _exits(f: pd.DataFrame, start: int, fill: float, legs, eod_tod: int) -> tuple[list[float], bool]:
    """Exit price per leg walking bars from index ``start``; also whether +5% came before a stop."""
    out: list[float | None] = [None] * len(legs)
    stop = legs[0].stop
    green = False
    i = start
    while i < len(f) and f.tod.iloc[i] < eod_tod:
        o, h, lo = f.open.iloc[i], f.high.iloc[i], f.low.iloc[i]
        if lo <= stop:
            px = min(stop, o)
            out = [px if x is None else x for x in out]
            return out, green
        green = green or bool(h >= fill * 1.05)
        for k, leg in enumerate(legs):
            if out[k] is None and h >= leg.take_profit:
                out[k] = leg.take_profit
        if all(x is not None for x in out):
            return out, green
        i += 1
    eod = f.open.iloc[i] if i < len(f) else f.close.iloc[-1]
    return [eod if x is None else x for x in out], green


def simulate(idea: StockIdea, posted: pd.Timestamp, f: pd.DataFrame | None, lim: Limits = Limits()) -> dict:
    if f is None or f.empty:
        return {"status": "no_bars"}
    done = f[f.ts + pd.Timedelta(minutes=1) <= posted]
    last = float(done.close.iloc[-1]) if len(done) else None
    plan = decide_idea(idea, posted.to_pydatetime(), posted.to_pydatetime(), last, State(), lim)
    if not isinstance(plan, IdeaPlan):
        return {"status": "skip", "reason": plan.reason}
    start_ts = posted.ceil("min")
    end_ts = posted + plan.cancel_after
    win = f[(f.ts >= start_ts) & (f.ts < end_ts) & (f.tod >= 9 * 60 + 30) & (f.tod < 16 * 60)]
    out = {"status": "no_fill", "limit": plan.limit, "last": last}
    eod = lim.flatten_at.hour * 60 + lim.flatten_at.minute
    hit = win[win.low <= plan.limit]
    if len(hit):
        j = hit.index[0]
        fill = float(min(plan.limit, f.open.loc[j]))
        out.update(_score(f, j, fill, plan, eod, idea, "limit"))
    if len(win):  # comparison: market entry at the first eligible bar's open (secondary)
        j = win.index[0]
        m = _score(f, j, float(f.open.loc[j]), plan, eod, idea, "mkt")
        out.update({k: v for k, v in m.items() if k.startswith("mkt_")})
    return out


def _score(f, j, fill, plan, eod, idea, tag) -> dict:
    exits, green = _exits(f, f.index.get_loc(j) + 1, fill, plan.legs, eod)
    qty = np.array([l.qty for l in plan.legs], float)
    gross = float((qty * (np.array(exits) - fill)).sum() / (qty.sum() * fill))
    cost = COST.round_trip(idea.symbol, f.ts.loc[j].to_pydatetime(), fill)[0]
    res = {"fill": fill, "fill_ts": f.ts.loc[j], "gross": gross, "cost": cost, "net": gross - cost,
           "green5": green, "stopped": bool(min(exits) <= plan.legs[0].stop)}
    if tag == "limit":
        return {"status": "filled", **res}
    return {f"mkt_{k}": v for k, v in res.items()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="sgt.db")
    ap.add_argument("--data", default="data/alpaca")
    ap.add_argument("--dry-run", action="store_true", help="counts and coverage only; no returns")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    calls = load_calls(args.db)
    store = ParquetBarStore(args.data)
    rows = []
    for r in calls.itertuples(index=False):
        bars = store.read(r.symbol, r.session, r.session)
        res = simulate(r.idea, r.posted_at, _frame(bars) if bars else None)
        rows.append({"symbol": r.symbol, "session": r.session, "posted_at": r.posted_at,
                     "high_risk": r.idea.high_risk, **res})
    t = pd.DataFrame(rows)
    st = t.status.value_counts().to_dict()
    eligible = t[t.status != "skip"]
    guard = float((eligible.status == "no_bars").mean()) if len(eligible) else 1.0
    filled = t[t.status == "filled"].sort_values("posted_at")
    d_star = filled.posted_at.quantile(0.75, interpolation="lower").tz_convert(ET).date()
    tr, ho = filled[filled.session < d_star], filled[filled.session >= d_star]
    print(f"calls {len(t)} status {st} | data guard {guard:.1%} | D* {d_star} | train {len(tr)} holdout {len(ho)}")
    if args.dry_run:
        return 0

    rng = np.random.default_rng(SEED)
    x = tr.net.to_numpy(float)
    lb = float(np.quantile(boot_means(x, tr.session.astype(str).to_numpy(), rng), 0.05))
    t5 = top5_share(x)
    train_pass = len(tr) >= 100 and lb > 0 and t5 < 0.5
    print(f"\nTRAINING n={len(tr)} mean net {x.mean():.2%} median {np.median(x):.2%} win {(x > 0).mean():.0%} "
          f"lower95 {lb:.2%} top5 {t5:.2f} gross {tr.gross.mean():.2%} cost {tr.cost.mean():.2%} "
          f"-> {'pass' if train_pass else 'fail'}")
    verdict, hold = "FAIL", None
    if guard > 0.20:
        verdict = "INCONCLUSIVE (data)"
    elif train_pass:
        y = ho.net.to_numpy(float)
        hold = {"n": len(y), "mean": float(y.mean()) if len(y) else None, "top5": top5_share(y)}
        print(f"HELD-OUT n={len(y)} mean net {y.mean():.2%} win {(y > 0).mean():.0%} top5 {hold['top5']:.2f}")
        verdict = ("INCONCLUSIVE" if len(y) < 30 else
                   "PASS" if (y.mean() > 0 and hold["top5"] < 0.5) else "FAIL")
    else:
        print("held-out not examined (training failed)")
    print(f"\nVERDICT: {verdict}")

    tre = t[(t.session < d_star) & t.status.isin(["filled", "no_fill"])]
    print("\nSECONDARY (training only)")
    print(f"fill rate {(tre.status == 'filled').mean():.0%} | reached +5% before stop {tr.green5.mean():.0%} "
          f"| stopped {tr.stopped.mean():.0%} | market-entry mean net {tre.mkt_net.mean():.2%} (n={tre.mkt_net.notna().sum()})")
    for name, g in (("HIGH RISK", tr[tr.high_risk]), ("normal", tr[~tr.high_risk])):
        if len(g):
            print(f"{name:10s} n={len(g)} net {g.net.mean():.2%} win {(g.net > 0).mean():.0%}")
    tod = tr.fill_ts.dt.tz_convert(ET).dt.hour
    print(tr.groupby(np.where(tod < 10, "09:xx", np.where(tod < 12, "10-11", "12+")))
          .net.agg(["size", "mean"]).to_string(float_format=lambda v: f"{v:.2%}"))
    t.to_csv(OUT / "trades.csv", index=False)
    summary = {"d_star": str(d_star), "status_counts": st, "guard": guard, "train_n": len(tr),
               "train_mean": float(x.mean()), "train_lower95": lb, "top5": t5, "holdout": hold, "verdict": verdict}
    (OUT / "result.json").write_text(json.dumps(summary, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="trade-ideas-2026-10-29", kind="trade-ideas",
                                  params={"prereg": "docs/findings/prereg_trade_ideas_2026-10-29.md"},
                                  metrics=json.loads(json.dumps(summary, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
