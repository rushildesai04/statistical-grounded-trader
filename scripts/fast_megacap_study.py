"""Fast learned large-cap books (30 min .. close): build rows, develop, score the held-out ONCE.

Rules: docs/findings/prereg_fast_megacap_2026-11-03.md. Vault study ``fast-megacap-2026-11``.

    python scripts/fast_megacap_study.py build    # rows per name (cached); counts only, no label statistics
    python scripts/fast_megacap_study.py dev      # 16 configurations on development data, selection
    python scripts/fast_megacap_study.py score    # unlocks the vault ONCE; held-out verdict + secondary
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from megacap_study import adjusted_daily
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast.learn import (FEATS, HORIZONS, QS, build_rows, horizon_frame, lower_bound, name_book,
                            name_positions, sharpe, walk_forward_name, walk_forward_pooled, xs_book)
from sgt.intraday.store import ParquetBarStore

STUDY = "fast-megacap-2026-11"
TICKERS = ["AAPL", "AMZN", "GOOGL", "MSFT", "META", "NVDA", "TSLA",
           "AVGO", "NFLX", "COST", "JPM", "V", "MA", "LLY", "UNH", "XOM", "WMT", "ORCL", "AMD"]
START, END = date(2016, 1, 4), date(2026, 10, 29)
WF_START = "2018-01"
COST = 0.0004
SEED = 20261103
MIN_DEV_SHARPE = 0.5
CACHE = Path("data/fast_megacap")
OUT = Path("reports/fast_megacap")


def rows(sym: str, store: ParquetBarStore, spy: pd.DataFrame, qqq: pd.DataFrame) -> pd.DataFrame:
    path = CACHE / f"rows_{sym}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    bars = store.read_frame(sym, START, END)
    if bars.empty:
        raise SystemExit(f"{sym}: no 1-minute bars in the store")
    df = build_rows(bars, adjusted_daily(sym), spy, qqq)
    df.to_parquet(path)
    return df


def load_all() -> dict[str, pd.DataFrame]:
    CACHE.mkdir(parents=True, exist_ok=True)
    store = ParquetBarStore("data/alpaca")
    spy, qqq = store.read_frame("SPY", START, END), store.read_frame("QQQ", START, END)
    out = {}
    for s in TICKERS:
        out[s] = rows(s, store, spy, qqq)
        print(f"{s:5s} rows {len(out[s]):6d} sessions {out[s].session.nunique():5d}", flush=True)
    return out


def run_all(data: dict[str, pd.DataFrame], days) -> dict[str, dict]:
    """Every configuration -> {"cfg", "net" (4 bp), "net2x", "trades", "gross_per_trade"} on ``days``."""
    res = {}
    for h in HORIZONS:
        frames = {s: horizon_frame(d, h) for s, d in data.items()}
        per = {s: walk_forward_name(f, WF_START) for s, f in frames.items()}
        for s, t in per.items():
            t.to_parquet(OUT / f"wf_{h}_{s}.parquet")
        for q in QS:
            pos = {s: name_positions(t, q) for s, t in per.items() if not t.empty}
            n = sum(int((p != 0).sum()) for p in pos.values())
            g = sum(float((p * per[s].label).sum()) for s, p in pos.items())
            res[f"name_H{h}_q{q}"] = {"cfg": {"book": "name", "H": h, "q": q},
                                      "net": name_book(per, q, COST, len(TICKERS), days),
                                      "net2x": name_book(per, q, 2 * COST, len(TICKERS), days),
                                      "trades": n, "gross_per_trade": g / n if n else float("nan")}
        pooled = walk_forward_pooled(frames, WF_START)
        pooled.to_parquet(OUT / f"wf_pooled_{h}.parquet")
        res[f"xs_H{h}"] = {"cfg": {"book": "xs", "H": h}, "net": xs_book(pooled, COST, days),
                           "net2x": xs_book(pooled, 2 * COST, days), "trades": None, "gross_per_trade": None}
        print(f"H={h} done", flush=True)
    return res


def summarize(r: dict, days_mask=None) -> dict:
    net, n2 = r["net"], r["net2x"]
    if days_mask is not None:
        net, n2 = net[days_mask], n2[days_mask]
    return {"mean": float(net.mean()), "sharpe": sharpe(net), "mean2x": float(n2.mean()),
            "ann": float(net.mean() * 252), "trades": r["trades"], "gross_per_trade": r["gross_per_trade"]}


def select(table: dict[str, dict]) -> str | None:
    ok = {k: v for k, v in table.items() if v["mean2x"] > 0 and v["sharpe"] == v["sharpe"]}
    if not ok:
        return None
    best = max(ok, key=lambda k: ok[k]["sharpe"])
    return best if ok[best]["sharpe"] >= MIN_DEV_SHARPE else None


def print_table(title: str, table: dict[str, dict]) -> None:
    print(f"\n{title}")
    for k, v in table.items():
        gpt = "" if v["gross_per_trade"] is None else f" gross/trade {v['gross_per_trade'] * 1e4:+.1f}bp trades {v['trades']}"
        print(f"  {k:22s} mean {v['mean']:+.4%}/day ann {v['ann']:+.1%} Sharpe {v['sharpe']:+.2f} "
              f"2x-cost {v['mean2x']:+.4%}{gpt}")


def sessions(data: dict[str, pd.DataFrame], lo: date, hi: date) -> pd.Index:
    s = sorted({x for d in data.values() for x in d.session.unique() if lo <= x <= hi})
    return pd.Index(s, name="session")


def cmd_dev(vault) -> int:
    data = {s: vault.train_rows(d, "session").reset_index(drop=True) for s, d in load_all().items()}
    days = sessions(data, date(2018, 1, 1), vault.holdout_start)
    res = run_all(data, days)
    table = {k: summarize(r) for k, r in res.items()}
    print_table(f"DEVELOPMENT {days.min()} .. {days.max()} ({len(days)} days), 4 bp round trip", table)
    pick = select(table)
    print(f"\nSELECTED: {pick or 'none (no config with dev Sharpe >= 0.5 and 2x-cost mean > 0): STOP, vault stays locked'}")
    (OUT / "dev.json").write_text(json.dumps({"selected": pick, "table": table}, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fast-megacap-2026-11-03-dev", kind="fast-megacap-dev",
                                  params={"prereg": "docs/findings/prereg_fast_megacap_2026-11-03.md"},
                                  metrics=json.loads(json.dumps({"selected": pick, "table": table}, default=str)))
    return 0


def cmd_score(vault) -> int:
    dev = json.loads((OUT / "dev.json").read_text())
    if not dev["selected"]:
        raise SystemExit("development selected nothing: the prereg says STOP; the vault stays locked")
    vault.unlock("scripts/fast_megacap_study.py")
    data = {s: vault.frame(d[d.session <= vault.study.holdout[1]], "session").reset_index(drop=True)
            for s, d in load_all().items()}
    days = sessions(data, date(2018, 1, 1), vault.study.holdout[1])
    res = run_all(data, days)
    dev_mask = days < vault.holdout_start
    again = select({k: summarize(r, dev_mask) for k, r in res.items()})
    if again != dev["selected"]:
        raise SystemExit(f"selection not reproduced on the full walk-forward: {again} vs {dev['selected']}")
    ho = ~dev_mask
    r = res[dev["selected"]]
    net, n2 = r["net"][ho], r["net2x"][ho]
    rng = np.random.default_rng(SEED)
    lb = lower_bound(net.to_numpy(float), 0.05, rng)
    years = net.groupby(pd.to_datetime(pd.Series(net.index)).dt.year.to_numpy()).sum()
    checks = {"lower95>0": lb > 0, "sharpe>=1": sharpe(net) >= 1.0, "2x_cost_mean>0": n2.mean() > 0,
              "years_pos>=2of3": int((years > 0).sum()) >= 2}
    verdict = "PASS" if all(checks.values()) else "FAIL"
    print(f"\nHELD-OUT {net.index.min()} .. {net.index.max()} ({len(net)} days) config {dev['selected']}")
    print(f"mean daily net {net.mean():+.4%} lower95 {lb:+.4%} Sharpe {sharpe(net):+.2f} ann {net.mean() * 252:+.1%} "
          f"2x-cost {n2.mean():+.4%}")
    print("by year:", {int(y): f"{v:+.1%}" for y, v in years.items()})
    print("checks:", checks)
    print(f"VERDICT: {verdict}")
    summary = {"verdict": verdict, "selected": dev["selected"], "checks": checks, "mean": float(net.mean()),
               "lower95": lb, "sharpe": sharpe(net), "years": {int(y): float(v) for y, v in years.items()}}
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fast-megacap-2026-11-03", kind="fast-megacap",
                                  params={"prereg": "docs/findings/prereg_fast_megacap_2026-11-03.md",
                                          "vault_commit": vault.commit},
                                  metrics=json.loads(json.dumps(summary, default=str)))

    # Secondary (recorded verdict above is final): every configuration on the held-out period.
    print_table("SECONDARY: all configurations, held-out", {k: summarize(v, ho) for k, v in res.items()})
    summary["secondary"] = {k: summarize(v, ho) for k, v in res.items()}
    (OUT / "result.json").write_text(json.dumps(summary, indent=2, default=str))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["build", "dev", "score"])
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    from sgt.research.vault import Vault

    vault = Vault(STUDY)
    if args.cmd == "build":
        data = load_all()
        print("feature NaN share:", {s: f"{d[FEATS[:-1]].isna().mean().mean():.1%}" for s, d in data.items()})
        return 0
    return {"dev": cmd_dev, "score": cmd_score}[args.cmd](vault)


if __name__ == "__main__":
    raise SystemExit(main())
