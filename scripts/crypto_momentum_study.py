"""C2M momentum after alt bursts (prereg_crypto_momentum_2026-11-05).

    python scripts/crypto_momentum_study.py dev       # training 2024-01..2026-04 (reported; not independent)
    python scripts/crypto_momentum_study.py holdout   # ONE-SHOT: vault unlock, download 2026-05..10, score
"""
from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd

from crypto_fetch import ROOT, train_months
from sgt.crypto import coinbase, leadlag, pumpfade
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.research.vault import Vault, load_registry

STUDY = "crypto-momentum-2026-11"
PREREG = "docs/findings/prereg_crypto_momentum_2026-11-05.md"
OUT = Path("reports/crypto_momentum")
DEV_MID = pd.Timestamp("2025-03-01", tz="UTC")
HO_MID = pd.Timestamp("2026-08-01", tz="UTC")
WARMUP = "2026-04"                                       # last training month: the 30-day threshold window only


def trades(coins: dict[str, pd.DataFrame], btc: pd.DataFrame, since: pd.Timestamp | None = None, **kw) -> pd.DataFrame:
    parts = []
    for s, f in coins.items():
        ev = pumpfade.events(f, btc)
        if since is not None:
            ev = ev[ev.ts >= since]
        if len(ev):
            parts.append(pumpfade.trade(f, ev, side=1, **kw).assign(coin=s))
    return pd.concat(parts, ignore_index=True).sort_values("ts") if parts else pd.DataFrame(
        columns=["ts", "run", "entry", "cover", "stopped", "gross", "net", "coin"])


def report(name: str, t: pd.DataFrame, mid: pd.Timestamp, coins, btc, since=None) -> dict:
    res = {"primary": leadlag.gate(t, mid, np.random.default_rng(pumpfade.SEED)), "secondary": {}}
    sec = res["secondary"]
    for hold in (15, 30, 120):
        h = trades(coins, btc, since, hold=hold)
        sec[f"hold_{hold}"] = {"n": len(h), "mean": float(h.net.mean()) if len(h) else None}
    for cost in (0.0002, 0.003, 0.012):
        sec[f"cost_{cost}"] = float((t.gross - cost).mean()) if len(t) else None
    sec["per_coin"] = {s: {"n": len(g), "mean": float(g.net.mean())} for s, g in t.groupby("coin")}
    p = res["primary"]
    print(f"{name}: n {p['n']} mean net {p.get('mean', float('nan')):+.3%} gross {p.get('gross', float('nan')):+.3%} "
          f"hit {p.get('hit', float('nan')):.0%} lower95 {p.get('lower95', float('nan')):+.3%} halves "
          f"{p.get('halves')} top month {p.get('top_month_share')}")
    for k, v in sec.items():
        if k != "per_coin":
            print(f"  {k}: {v}")
    print(f"VERDICT ({name}): {'PASS' if p['pass'] else 'FAIL'}")
    return res


def save(kind: str, res: dict, t: pd.DataFrame) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{kind}.json").write_text(json.dumps(res, indent=2, default=str))
    t.to_csv(OUT / f"{kind}_trades.csv", index=False)
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id=f"crypto-momentum-2026-11-05-{kind}", kind=f"crypto-momentum-{kind}",
                                  params={"prereg": PREREG}, metrics=json.loads(json.dumps(res, default=str)))


def cmd_dev() -> int:
    months = train_months(STUDY)
    btc = coinbase.load(ROOT, coinbase.LEADER, months)
    coins = {p: coinbase.load(ROOT, p, months) for p in coinbase.ALTS}
    t = trades(coins, btc)
    save("dev", report("DEV (not independent)", t, DEV_MID, coins, btc), t)
    return 0


def holdout_months() -> list[str]:
    lo, hi = load_registry()[STUDY].holdout
    return [p.strftime("%Y-%m") for p in pd.period_range(lo, hi, freq="M")]


def cmd_holdout() -> int:
    head = Vault(STUDY).unlock(scorer="scripts/crypto_momentum_study.py")       # one-shot, recorded, needs clean commit
    print(f"vault {STUDY} unlocked at {head}")
    months = holdout_months()
    with coinbase.client() as c:
        for prod in (coinbase.LEADER, *coinbase.ALTS):
            for m in months:
                coinbase.fetch_month(c, ROOT, prod, m)
    use = [WARMUP, *months]
    btc = coinbase.load(ROOT, coinbase.LEADER, use)
    coins = {p: coinbase.load(ROOT, p, use) for p in coinbase.ALTS}
    since = pd.Timestamp(months[0] + "-01", tz="UTC")
    t = trades(coins, btc, since)
    save("holdout", report("HELD-OUT (decisive)", t, HO_MID, coins, btc, since), t)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev", "holdout"])
    return {"dev": cmd_dev, "holdout": cmd_holdout}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
