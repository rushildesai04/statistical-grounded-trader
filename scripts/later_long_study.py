"""Buy the channel's later alerts (prereg_later_long_2026-11-06; vault later-long-2026-11).

    python scripts/later_long_study.py dev       # training, REPORT ONLY (the hypothesis came from it)
    python scripts/later_long_study.py holdout   # ONE-SHOT: vault unlock, 2026-07-01 .. 2026-10-29, decides
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_forward import CHANNEL, DB
from fade_challengers import _rth
from fade_later_study import later_events
from sgt.crypto import leadlag
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast.exits import indicators, simulate_long
from sgt.intraday.store import ParquetBarStore
from sgt.research.vault import Vault, load_registry
from telegram_alert_test import attach_trades, load_alerts, primary_events

STUDY = "later-long-2026-11"
PREREG = "docs/findings/prereg_later_long_2026-11-06.md"
OUT = Path("reports/later_long")
SEED = 20261106


def run(lo: str, hi: str) -> pd.DataFrame:
    store = ParquetBarStore("data/alpaca")
    prim = primary_events(attach_trades(load_alerts(DB, CHANNEL), store))
    prim = prim.assign(session=prim.session.astype(str))
    prim = prim[(prim.session >= lo) & (prim.session <= hi)]
    ev = later_events(prim).reset_index(drop=True)
    rows = []
    for r in ev.itertuples(index=False):
        bars = _rth(store, r.symbol, r.session)
        if bars.empty:
            continue
        ind = indicators(bars.reset_index(drop=True))
        ts, px = pd.Timestamp(r.entry_ts), float(r.entry)
        out = {k: simulate_long(ind, ts, px, cap_min=k) for k in (30, 60, 120)}
        if out[60] is None:
            continue
        rows.append({"symbol": r.symbol, "session": r.session, "ts": ts, "alert_no": r.alert_no,
                     "alert_type": r.alert_type, "gross": out[60]["gross"], "net": out[60]["gross"] - float(r.cost_rt),
                     "how": out[60]["how"], **{f"net_{k}": (out[k]["gross"] - float(r.cost_rt)) if out[k] else np.nan
                                               for k in (30, 120)}})
    return pd.DataFrame(rows).sort_values("ts").reset_index(drop=True) if rows else pd.DataFrame()


def evaluate(t: pd.DataFrame, mid: str, min_n: int) -> dict:
    if t.empty:
        return {"primary": {"n": 0, "pass": False}, "secondary": {}}
    g = leadlag.gate(t, pd.Timestamp(mid, tz="UTC"), np.random.default_rng(SEED))
    if min_n < leadlag.MIN_EVENTS and g["n"] >= min_n and not g["pass"]:
        g["pass"] = bool(g["mean"] > 0 and g["lower95"] > 0 and min(g["halves"]) > 0 and g["top_month_share"] <= 0.5)
    et = t.ts.dt.tz_convert("America/New_York")
    win = pd.cut(et.dt.hour * 60 + et.dt.minute, [0, 660, 840, 1440], labels=["pre11", "11-14", "14-16"], right=False)
    sec = {"by_alert_no": {str(k): {"n": len(x), "mean": float(x.net.mean())} for k, x in t.groupby(t.alert_no.clip(upper=3))},
           "by_window": {str(k): {"n": len(x), "mean": float(x.net.mean())} for k, x in t.groupby(win, observed=True)},
           "hold_30": float(t.net_30.mean()), "hold_120": float(t.net_120.mean()),
           "stopped": float((t.how == "stop").mean())}
    return {"primary": g, "secondary": sec}


def report(name: str, res: dict) -> None:
    p = res["primary"]
    print(f"{name}: n {p['n']} mean net {p.get('mean', float('nan')):+.3%} gross {p.get('gross', float('nan')):+.3%} "
          f"hit {p.get('hit', float('nan')):.0%} lower95 {p.get('lower95', float('nan')):+.3%} halves {p.get('halves')} "
          f"top month {p.get('top_month_share')}")
    for k, v in res["secondary"].items():
        print(f"  {k}: {v}")
    print(f"VERDICT ({name}): {'PASS' if p['pass'] else 'FAIL'}")


def save(kind: str, res: dict, t: pd.DataFrame) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{kind}.json").write_text(json.dumps(res, indent=2, default=str))
    t.to_csv(OUT / f"{kind}_events.csv", index=False)
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id=f"later-long-2026-11-06-{kind}", kind=f"later-long-{kind}",
                                  params={"prereg": PREREG}, metrics=json.loads(json.dumps(res, default=str)))


def cmd_dev() -> int:
    lo, hi = (d.isoformat() for d in load_registry()[STUDY].train)
    t = run(lo, hi)
    res = evaluate(t, "2025-10-01", leadlag.MIN_EVENTS)
    report("DEV training (REPORT ONLY: the hypothesis came from it)", res)
    save("dev", res, t)
    return 0


def cmd_holdout() -> int:
    head = Vault(STUDY).unlock(scorer="scripts/later_long_study.py")
    print(f"vault {STUDY} unlocked at {head}")
    lo, hi = (d.isoformat() for d in load_registry()[STUDY].holdout)
    t = run(lo, hi)
    res = evaluate(t, "2026-09-01", 50)
    report("HELD-OUT (decisive, once)", res)
    save("holdout", res, t)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev", "holdout"])
    return {"dev": cmd_dev, "holdout": cmd_holdout}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
