"""Fade the channel's later alerts on the same stock (prereg_fade_later_alerts_2026-11-06).

    python scripts/fade_later_study.py dev       # training (sessions < 2026-07-01), run ONCE
    python scripts/fade_later_study.py holdout   # only after a dev PASS: 2026-07-01 .. 2026-10-29, run ONCE
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_forward import CHANNEL, DB
from alert_fade_test import score
from sgt.crypto import leadlag
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.store import ParquetBarStore
from telegram_alert_test import attach_trades, load_alerts, primary_events

PREREG = "docs/findings/prereg_fade_later_alerts_2026-11-06.md"
OUT = Path("reports/fade_later")
SEED, GAP = 20261106, pd.Timedelta(minutes=60)
SPLIT, HO_END = "2026-07-01", "2026-10-29"


def later_events(prim: pd.DataFrame) -> pd.DataFrame:
    """Non-first primary events, each >= 60 min after the previous KEPT entry of its symbol-day (first always kept)."""
    keep = []
    for _, g in prim.sort_values("posted_at").groupby(["symbol", "session"], sort=False):
        last, k = None, 0
        for i, r in g.iterrows():
            ts = pd.Timestamp(r.entry_ts)
            if last is None:
                last, k = ts, 1
                continue
            if ts - last >= GAP:
                k += 1
                keep.append((i, k))
                last = ts
    idx = [i for i, _ in keep]
    return prim.loc[idx].assign(alert_no=[k for _, k in keep])


def evaluate(t: pd.DataFrame, mid: str, min_n: int) -> dict:
    t = t.dropna(subset=["net"]).assign(ts=lambda d: pd.to_datetime(d.entry_ts, utc=True)).sort_values("ts")
    g = leadlag.gate(t.rename(columns={"gross_60": "gross"}) if "gross" not in t else t,
                     pd.Timestamp(mid, tz="UTC"), np.random.default_rng(SEED))
    if g["n"] and min_n < leadlag.MIN_EVENTS and g["n"] >= min_n and not g["pass"]:
        g["pass"] = bool(g["mean"] > 0 and g["lower95"] > 0 and min(g["halves"]) > 0 and g["top_month_share"] <= 0.5)
    et = t.ts.dt.tz_convert("America/New_York")
    win = pd.cut(et.dt.hour * 60 + et.dt.minute, [0, 660, 840, 1440], labels=["pre11", "11-14", "14-16"], right=False)
    sec = {"by_alert_no": {str(k): {"n": len(x), "mean": float(x.net.mean())}
                           for k, x in t.groupby(t.alert_no.clip(upper=3))},
           "by_window": {str(k): {"n": len(x), "mean": float(x.net.mean())} for k, x in t.groupby(win, observed=True)}}
    return {"primary": g, "secondary": sec}


def run(lo: str, hi: str) -> pd.DataFrame:
    store = ParquetBarStore("data/alpaca")
    prim = primary_events(attach_trades(load_alerts(DB, CHANNEL), store))
    prim = prim[(prim.session.astype(str) >= lo) & (prim.session.astype(str) <= hi)]
    ev = later_events(prim).reset_index(drop=True)
    sc = score(ev, store)
    return ev.assign(net=sc.net.to_numpy(), gross=sc.gross.to_numpy())


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
        trial_ledger.record_trial(s, run_id=f"fade-later-2026-11-06-{kind}", kind=f"fade-later-{kind}",
                                  params={"prereg": PREREG}, metrics=json.loads(json.dumps(res, default=str)))


def cmd_dev() -> int:
    t = run("2000-01-01", "2026-06-30")
    res = evaluate(t, "2025-10-01", leadlag.MIN_EVENTS)
    report("DEV training (decides)", res)
    save("dev", res, t)
    return 0


def cmd_holdout() -> int:
    if not json.loads((OUT / "dev.json").read_text())["primary"].get("pass"):
        raise SystemExit("development gate did not pass: held-out not scored (prereg)")
    t = run(SPLIT, HO_END)
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
