"""Pre-registered DAILY-horizon per-ticker models: build, walk forward, score ONCE.

Rules: docs/findings/prereg_megacap_daily_2026-10-30.md.

Usage:
    python scripts/megacap_daily_study.py --dry-run
    python scripts/megacap_daily_study.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from megacap_study import CACHE, COSTS, END, START, TICKERS, adjusted_daily, lower_bound, sharpe
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.store import ParquetBarStore
from sgt.megacap.daily import DAILY_FEATURES, build_daily
from sgt.megacap.walkforward import daily_portfolio, net_returns, walk_forward

PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=7, min_child_samples=100,
              colsample_bytree=0.8, subsample=0.8, subsample_freq=1, random_state=0, verbose=-1)
WF_START = "2018-01"
SEED = 20261030
OUT = Path("reports/megacap_daily")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    store = ParquetBarStore("data/alpaca")
    spy_b, qqq_b = store.read_frame("SPY", START, END), store.read_frame("QQQ", START, END)
    spy_d, qqq_d = adjusted_daily("SPY"), adjusted_daily("QQQ")
    data = {}
    for s in TICKERS:
        path = CACHE / f"dfeat_{s}.parquet"
        if not path.exists():
            build_daily(store.read_frame(s, START, END), adjusted_daily(s), spy_b, spy_d, qqq_b, qqq_d).to_parquet(path)
        data[s] = pd.read_parquet(path)
        d = data[s]
        print(f"{s:6s} rows {len(d):5d} {d.session.min()} .. {d.session.max()} "
              f"NaN share {d[DAILY_FEATURES].isna().mean().mean():.1%}")
    if args.dry_run:
        return 0

    feats = list(DAILY_FEATURES)
    trades = {s: walk_forward(d, WF_START, features=feats, params=PARAMS) for s, d in data.items()}
    for s, t in trades.items():
        t.to_parquet(OUT / f"oos_{s}.parquet")
    rng = np.random.default_rng(SEED)
    p1, p2 = daily_portfolio(trades, COSTS, 1.0), daily_portfolio(trades, COSTS, 2.0)
    lb = lower_bound(p1.to_numpy(float), 0.05, rng)
    years = p1.groupby(pd.to_datetime(p1.index).year).sum()
    recent = p1[pd.to_datetime(p1.index) >= "2025-01-01"]
    checks = {"lower95>0": bool(lb > 0), "sharpe>=1": bool(sharpe(p1) >= 1.0), "2x_cost_mean>0": bool(p2.mean() > 0),
              "years_pos>=6": bool((years > 0).sum() >= 6), "2025+_mean>0": bool(recent.mean() > 0)}
    verdict = "PASS" if all(checks.values()) else "FAIL"
    print(f"\nPORTFOLIO {p1.index.min()} .. {p1.index.max()} ({len(p1)} days)")
    print(f"mean daily net {p1.mean():.4%}  lower95 {lb:.4%}  Sharpe {sharpe(p1):.2f}  ann. {p1.mean() * 252:.1%}  "
          f"2x-cost mean {p2.mean():.4%}  2025+ mean {recent.mean():.4%}")
    print("by year:", {int(y): f"{v:.1%}" for y, v in years.items()})
    print("checks:", checks, f"\nVERDICT: {verdict}")

    print("\nPER TICKER (Bonferroni 0.05/8; only matters if the portfolio passes)")
    per = {}
    for s, t in trades.items():
        net = net_returns(t, COSTS[s]).groupby(t.session).sum()
        tl = lower_bound(net.to_numpy(float), 0.05 / len(TICKERS), rng)
        gross = (t.position * t.label)[t.position != 0]
        ic = t.groupby(pd.to_datetime(t.session).dt.year).apply(lambda g: g.pred.corr(g.label, method="spearman"))
        per[s] = {"days": len(net), "trades": int((t.position != 0).sum()), "gross_bp": float(gross.mean() * 1e4),
                  "hit": float((gross > 0).mean()), "mean_daily": float(net.mean()), "lower": tl,
                  "sharpe": sharpe(net), "ic": float(ic.mean())}
        print(f"{s:6s} trades {per[s]['trades']:5d} gross/trade {per[s]['gross_bp']:+6.1f} bp hit {per[s]['hit']:.0%} "
              f"daily net {net.mean():+.4%} Sharpe {sharpe(net):+.2f} IC {ic.mean():+.3f} "
              f"continue={'yes' if tl > 0 and verdict == 'PASS' else 'no'}")

    half = daily_portfolio(trades, COSTS, 0.5)
    lo = daily_portfolio({s: t.assign(position=t.position.clip(lower=0)) for s, t in trades.items()}, COSTS, 1.0)
    pooled_in = pd.concat([d.assign(tid=i) for i, d in enumerate(data.values())], ignore_index=True)
    pooled = walk_forward(pooled_in, WF_START, features=feats + ["tid"], params=PARAMS)
    pp = daily_portfolio({s: pooled[pooled.tid == i] for i, s in enumerate(data)}, COSTS, 1.0)
    print(f"\nSECONDARY: 0.5x costs Sharpe {sharpe(half):.2f} | long-only Sharpe {sharpe(lo):.2f} "
          f"(mean {lo.mean():.4%}) | pooled Sharpe {sharpe(pp):.2f}")

    summary = {"verdict": verdict, "checks": checks, "mean_daily": float(p1.mean()), "lower95": lb,
               "sharpe": sharpe(p1), "years": {int(y): float(v) for y, v in years.items()}, "per_ticker": per,
               "secondary": {"half_cost_sharpe": sharpe(half), "long_only_sharpe": sharpe(lo), "pooled_sharpe": sharpe(pp)}}
    (OUT / "result.json").write_text(json.dumps(summary, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="megacap-daily-2026-10-30", kind="megacap-daily",
                                  params={"prereg": "docs/findings/prereg_megacap_daily_2026-10-30.md"},
                                  metrics=json.loads(json.dumps(summary, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
