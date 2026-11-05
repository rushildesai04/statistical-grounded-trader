"""C2 alt pump fade (prereg_crypto_pumpfade_2026-11-05): development gate on training months, run ONCE.

    python scripts/crypto_pumpfade_study.py dev
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from crypto_fetch import ROOT, train_months
from sgt.crypto import coinbase, leadlag, pumpfade
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger

STUDY = "crypto-leadlag-2026-11"                         # same vault split as C1 (held-out still sealed)
OUT = Path("reports/crypto_pumpfade")
MID = pd.Timestamp("2025-03-01", tz="UTC")


def run(coins: dict[str, pd.DataFrame], ev: dict[str, pd.DataFrame], **kw) -> pd.DataFrame:
    parts = [pumpfade.trade(coins[s], e, **kw).assign(coin=s) for s, e in ev.items() if len(e)]
    return pd.concat(parts, ignore_index=True).sort_values("ts") if parts else pd.DataFrame(
        columns=["ts", "run", "entry", "cover", "stopped", "gross", "net", "coin"])


def summary(t: pd.DataFrame) -> dict:
    return {"n": len(t), "mean": float(t.net.mean()) if len(t) else None,
            "gross": float(t.gross.mean()) if len(t) else None, "hit": float((t.net > 0).mean()) if len(t) else None}


def cmd_dev() -> int:
    months = train_months(STUDY)
    missing = [(p, m) for p in (coinbase.LEADER, *coinbase.ALTS) for m in months
               if not coinbase.month_path(ROOT, p, m).exists()]
    if missing:
        raise SystemExit(f"{len(missing)} product-months not downloaded yet (e.g. {missing[0]}); nothing computed")
    btc = coinbase.load(ROOT, coinbase.LEADER, months)
    coins = {p: coinbase.load(ROOT, p, months) for p in coinbase.ALTS}
    ev = {p: pumpfade.events(f, btc) for p, f in coins.items()}
    prim = run(coins, ev)
    res = {"primary": leadlag.gate(prim, MID, np.random.default_rng(pumpfade.SEED)), "secondary": {}}
    sec = res["secondary"]
    for hold in (15, 30, 120):
        sec[f"hold_{hold}"] = summary(run(coins, ev, hold=hold))
    for stop in (0.05, 0.20, None):
        sec[f"stop_{stop}"] = summary(run(coins, ev, stop=stop))
    for floor in (0.02, 0.05):
        sec[f"floor_{floor}"] = summary(run(coins, {p: pumpfade.events(f, btc, min_run=floor) for p, f in coins.items()}))
    sec["no_btc_excess"] = summary(run(coins, {p: pumpfade.events(f, btc, min_excess=None) for p, f in coins.items()}))
    for cost in (0.0002, 0.003, 0.012):
        sec[f"cost_{cost}"] = float((prim.gross - cost).mean()) if len(prim) else None
    sec["per_coin"] = {s: summary(g) for s, g in prim.groupby("coin")}
    if len(prim):
        hour = prim.ts.dt.tz_convert("America/New_York").dt.hour
        sec["us_session_share"] = float(((hour >= 9) & (hour < 16)).mean())
        sec["stop_rate"] = float(prim.stopped.mean())
    p = res["primary"]
    print(f"DEV C2 training {btc.ts.min():%Y-%m-%d} .. {btc.ts.max():%Y-%m-%d}: {len(prim)} trades")
    print(f"  primary (60 min, 10% stop, 0.10% cost): n {p['n']} mean net {p.get('mean', float('nan')):+.3%} gross "
          f"{p.get('gross', float('nan')):+.3%} hit {p.get('hit', float('nan')):.0%} lower95 "
          f"{p.get('lower95', float('nan')):+.3%} halves {p.get('halves')} top month {p.get('top_month_share')}")
    for k, v in sec.items():
        if k != "per_coin":
            print(f"  {k}: {v}")
    print(f"VERDICT (dev gate): {'PASS' if p['pass'] else 'FAIL'}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps(res, indent=2, default=str))
    prim.to_csv(OUT / "dev_trades.csv", index=False)
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="crypto-pumpfade-2026-11-05-dev", kind="crypto-pumpfade-dev",
                                  params={"prereg": "docs/findings/prereg_crypto_pumpfade_2026-11-05.md"},
                                  metrics=json.loads(json.dumps(res, default=str)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev"])
    return {"dev": cmd_dev}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
