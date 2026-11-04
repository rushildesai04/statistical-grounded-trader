"""C1 BTC lead-lag (prereg_crypto_leadlag_2026-11-04): development gate on training months, run ONCE.

    python scripts/crypto_leadlag_study.py dev
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from crypto_fetch import ROOT, train_months
from sgt.crypto import coinbase, leadlag
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger

STUDY = "crypto-leadlag-2026-11"
OUT = Path("reports/crypto_leadlag")
MID = pd.Timestamp("2025-03-01", tz="UTC")              # calendar midpoint of training 2024-01-01 .. 2026-04-30


def cmd_dev() -> int:
    months = train_months(STUDY)
    missing = [(p, m) for p in (coinbase.LEADER, *coinbase.ALTS) for m in months
               if not coinbase.month_path(ROOT, p, m).exists()]
    if missing:
        raise SystemExit(f"{len(missing)} product-months not downloaded yet (e.g. {missing[0]}); nothing computed")
    btc = coinbase.load(ROOT, coinbase.LEADER, months)
    alts = {p: coinbase.load(ROOT, p, months) for p in coinbase.ALTS}
    ev = leadlag.btc_events(btc)
    prim = leadlag.basket(ev, alts)
    res = {"primary": leadlag.gate(prim, MID, np.random.default_rng(leadlag.SEED)), "secondary": {}}
    sec = res["secondary"]
    for hold in (1, 3, 10):
        b = leadlag.basket(ev, alts, hold=hold)
        sec[f"hold_{hold}"] = {"n": len(b), "mean": float(b.net.mean()), "gross": float(b.gross.mean())}
    for q in (0.99, 0.999):
        b = leadlag.basket(leadlag.btc_events(btc, q=q), alts)
        sec[f"q_{q}"] = {"n": len(b), "mean": float(b.net.mean()), "gross": float(b.gross.mean())}
    for cost in (0.0002, 0.003, 0.012):
        sec[f"cost_{cost}"] = float((prim.gross - cost).mean())
    sec["per_alt_gross"] = {c[2:]: float(prim[c].mean()) for c in prim.columns if c.startswith("r_")}
    hour = prim.ts.dt.tz_convert("America/New_York").dt.hour
    sec["us_session_share"] = float(((hour >= 9) & (hour < 16)).mean())
    p = res["primary"]
    print(f"DEV C1 training {btc.ts.min():%Y-%m-%d} .. {btc.ts.max():%Y-%m-%d}: {len(ev)} BTC events")
    print(f"  primary (5 min, 0.10% cost): n {p['n']} mean net {p.get('mean', float('nan')):+.4%} gross "
          f"{p.get('gross', float('nan')):+.4%} hit {p.get('hit', float('nan')):.0%} lower95 "
          f"{p.get('lower95', float('nan')):+.4%} halves {p.get('halves')} top month {p.get('top_month_share')}")
    print("  secondary:", json.dumps({k: v for k, v in sec.items() if k != "per_alt_gross"}, default=str))
    print(f"VERDICT (dev gate): {'PASS' if p['pass'] else 'FAIL'}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps(res, indent=2, default=str))
    prim.to_csv(OUT / "dev_events.csv", index=False)
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="crypto-leadlag-2026-11-04-dev", kind="crypto-leadlag-dev",
                                  params={"prereg": "docs/findings/prereg_crypto_leadlag_2026-11-04.md"},
                                  metrics=json.loads(json.dumps(res, default=str)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev"])
    return {"dev": cmd_dev}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
