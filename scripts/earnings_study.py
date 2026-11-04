"""Earnings-reaction trades on large caps: fetch event times, develop, score the held-out ONCE.

Rules: docs/findings/prereg_earnings_2026-11-03.md. Vault study ``earnings-2026-11``.

    python scripts/earnings_study.py fetch    # EDGAR Item 2.02 8-K times + adjusted daily bars (no returns)
    python scripts/earnings_study.py dev      # 20 configurations on development events, selection
    python scripts/earnings_study.py score    # unlocks the vault ONCE; held-out verdict + secondary
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from megacap_study import adjusted_daily
from sgt.catalysts.earnings import CONFIGS, COST, Edgar, config_trades, date_lower_bound, event_prices, events
from sgt.core.secrets import get_secret
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger

STUDY = "earnings-2026-11"
TICKERS = ("AAPL MSFT AMZN GOOGL META NVDA TSLA AVGO JPM V MA LLY UNH XOM WMT ORCL AMD NFLX COST HD PG JNJ "
           "ABBV MRK PEP KO CVX BAC WFC C GS MS AXP BLK SCHW ADBE CRM CSCO INTC IBM QCOM TXN AMAT MU INTU NOW "
           "AMGN GILD BMY PFE TMO DHR ABT MDT ISRG CVS CI ELV MCD SBUX NKE LOW TGT BKNG DIS CMCSA T VZ TMUS CAT "
           "DE HON GE MMM BA LMT RTX UPS FDX UNP NEE DUK SO LIN COP SPG AMT PLD PYPL MO PM MDLZ CL KHC GM F USB "
           "ACN UBER").split()
DEV = (pd.Timestamp("2016-01-04"), pd.Timestamp("2023-12-29"))
SEED = 20261103
MIN_DEV_EVENTS, MIN_HO_EVENTS = 150, 100
CACHE = Path("data/earnings")
OUT = Path("reports/earnings")


def daily(sym: str) -> pd.DataFrame:
    d = adjusted_daily(sym)
    d.index = pd.to_datetime(d.index)
    return d.sort_index()


def cmd_fetch() -> int:
    CACHE.mkdir(parents=True, exist_ok=True)
    e = Edgar(get_secret("sec_contact"))
    try:
        ciks = e.ciks()
        missing = [t for t in TICKERS if t not in ciks]
        if missing:
            raise SystemExit(f"no CIK for {missing}")
        for t in TICKERS:
            path = CACHE / f"raw_{t}.parquet"
            if not path.exists():
                e.earnings(ciks[t]).to_parquet(path)
            daily(t)
            print(f"{t:5s} filings {len(pd.read_parquet(path)):3d}", flush=True)
    finally:
        e.close()
    daily("SPY")
    return 0


def all_prices() -> pd.DataFrame:
    spy = daily("SPY")
    sessions = spy.index
    out = []
    for t in TICKERS:
        ev = events(pd.read_parquet(CACHE / f"raw_{t}.parquet"), sessions)
        p = event_prices(ev, daily(t), spy)
        out.append(p.assign(sym=t))
    return pd.concat(out, ignore_index=True)


def table(prices: pd.DataFrame, rng) -> dict[str, dict]:
    res = {}
    for k, cfg in CONFIGS.items():
        t = config_trades(prices, cfg)
        t2 = config_trades(prices, cfg, 2 * COST)
        res[k] = {"n": len(t), "mean": float(t.net.mean()) if len(t) else float("nan"),
                  "lower95": date_lower_bound(t, 0.05, rng), "mean2x": float(t2.net.mean()) if len(t2) else float("nan"),
                  "raw_mean": float(t.raw.mean()) if len(t) else float("nan"),
                  "years": {int(y): float(v) for y, v in t.net.groupby(pd.to_datetime(t.R).dt.year).mean().items()}}
    return res


def print_table(title: str, tab: dict[str, dict]) -> None:
    print(f"\n{title}")
    for k, v in tab.items():
        print(f"  {k:22s} n {v['n']:5d} mean {v['mean']:+.3%} lower95 {v['lower95']:+.3%} 2x {v['mean2x']:+.3%} "
              f"unhedged {v['raw_mean']:+.3%}")


def select(tab: dict[str, dict]) -> str | None:
    ok = {k: v for k, v in tab.items() if v["n"] >= MIN_DEV_EVENTS and v["lower95"] == v["lower95"]}
    if not ok:
        return None
    best = max(ok, key=lambda k: ok[k]["lower95"])
    return best if ok[best]["lower95"] > 0 else None


def window(p: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    """Events whose reaction session AND last possible exit (R+5) lie inside [lo, hi]."""
    return p[(p.R >= lo) & (p.d5 <= hi)].reset_index(drop=True)


def cmd_dev(vault) -> int:
    p = window(all_prices(), *DEV)
    vault.frame(p, "d5")
    tab = table(p, np.random.default_rng(SEED))
    print_table(f"DEVELOPMENT events {p.R.min().date()} .. {p.R.max().date()} ({len(p)} events, {p.sym.nunique()} names)", tab)
    pick = select(tab)
    print(f"\nSELECTED: {pick or 'none (best lower95 <= 0): STOP, vault stays locked'}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps({"selected": pick, "table": tab}, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="earnings-2026-11-03-dev", kind="earnings-dev",
                                  params={"prereg": "docs/findings/prereg_earnings_2026-11-03.md"},
                                  metrics=json.loads(json.dumps({"selected": pick, "table": tab}, default=str)))
    return 0


def cmd_score(vault) -> int:
    dev = json.loads((OUT / "dev.json").read_text())
    if not dev["selected"]:
        raise SystemExit("development selected nothing: the prereg says STOP; the vault stays locked")
    vault.unlock("scripts/earnings_study.py")
    allp = all_prices()
    again = select(table(window(allp, *DEV), np.random.default_rng(SEED)))
    if again != dev["selected"]:
        raise SystemExit(f"selection not reproduced: {again} vs {dev['selected']}")
    lo, hi = pd.Timestamp(vault.holdout_start), pd.Timestamp(vault.study.holdout[1])
    ho = vault.frame(window(allp, lo, hi), "d5")
    rng = np.random.default_rng(SEED)
    cfg = CONFIGS[dev["selected"]]
    t, t2 = config_trades(ho, cfg), config_trades(ho, cfg, 2 * COST)
    lb = date_lower_bound(t, 0.05, rng)
    years = t.net.groupby(pd.to_datetime(t.R).dt.year).mean()
    checks = {"lower95>0": lb > 0, "2x_cost_mean>0": bool(t2.net.mean() > 0),
              "years_pos>=2of3": int((years > 0).sum()) >= 2, "events>=100": len(t) >= MIN_HO_EVENTS}
    verdict = "PASS" if all(checks.values()) else "FAIL"
    print(f"\nHELD-OUT {lo.date()} .. {hi.date()} config {dev['selected']}: {len(t)} events")
    print(f"mean net/event {t.net.mean():+.3%} lower95 {lb:+.3%} 2x-cost {t2.net.mean():+.3%} unhedged {t.raw.mean():+.3%}")
    print("by year:", {int(y): f"{v:+.3%}" for y, v in years.items()})
    print("checks:", checks)
    print(f"VERDICT: {verdict}")
    summary = {"verdict": verdict, "selected": dev["selected"], "checks": checks, "n": len(t),
               "mean": float(t.net.mean()), "lower95": lb, "years": {int(y): float(v) for y, v in years.items()}}
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="earnings-2026-11-03", kind="earnings",
                                  params={"prereg": "docs/findings/prereg_earnings_2026-11-03.md",
                                          "vault_commit": vault.commit},
                                  metrics=json.loads(json.dumps(summary, default=str)))
    sec = table(ho, np.random.default_rng(SEED))
    print_table("SECONDARY: all configurations, held-out", sec)
    summary["secondary"] = sec
    (OUT / "result.json").write_text(json.dumps(summary, indent=2, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["fetch", "dev", "score"])
    args = ap.parse_args()
    if args.cmd == "fetch":
        return cmd_fetch()
    from sgt.research.vault import Vault

    vault = Vault(STUDY)
    return {"dev": cmd_dev, "score": cmd_score}[args.cmd](vault)


if __name__ == "__main__":
    raise SystemExit(main())
