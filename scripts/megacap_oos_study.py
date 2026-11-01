"""Pre-registered out-of-sample test: daily per-ticker models on 12 unseen large caps, scored ONCE.

Rules: docs/findings/prereg_megacap_oos_2026-10-30.md. Same code and parameters as
scripts/megacap_daily_study.py (v2, 1-session embargo); only the tickers differ.

Usage:
    python scripts/megacap_oos_study.py --dry-run
    python scripts/megacap_oos_study.py
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from megacap_daily_study import PARAMS, SEED, WF_START
from megacap_study import CACHE, END, START, adjusted_daily, lower_bound, sharpe
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.store import ParquetBarStore
from sgt.megacap.daily import DAILY_FEATURES, build_daily
from sgt.megacap.walkforward import daily_portfolio, net_returns, walk_forward

TICKERS = ["AVGO", "NFLX", "COST", "JPM", "V", "MA", "LLY", "UNH", "XOM", "WMT", "ORCL", "AMD"]
ORIGINAL = ["AAPL", "AMZN", "GOOGL", "MSFT", "META", "NVDA"]
COSTS = {t: 0.0004 for t in TICKERS + ORIGINAL}
ORIGINAL_OOS = Path("reports/megacap_daily_v2")
OUT = Path("reports/megacap_oos")


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
        sessions = store.sessions(s)
        if len(sessions) < 2500:
            raise SystemExit(f"{s}: only {len(sessions)} sessions in the 1-minute store; backfill first")
        path = CACHE / f"dfeat_{s}.parquet"
        if not path.exists():
            build_daily(store.read_frame(s, START, END), adjusted_daily(s), spy_b, spy_d, qqq_b, qqq_d).to_parquet(path)
        data[s] = d = pd.read_parquet(path)
        print(f"{s:6s} sessions {len(sessions)} rows {len(d):5d} {d.session.min()} .. {d.session.max()} "
              f"NaN share {d[DAILY_FEATURES].isna().mean().mean():.1%}")
    if args.dry_run:
        return 0

    feats = list(DAILY_FEATURES)
    trades = {s: walk_forward(d, WF_START, features=feats, params=PARAMS, embargo_sessions=1) for s, d in data.items()}
    for s, t in trades.items():
        t.to_parquet(OUT / f"oos_{s}.parquet")
    rng = np.random.default_rng(SEED)
    p1, p2 = daily_portfolio(trades, COSTS, 1.0), daily_portfolio(trades, COSTS, 2.0)
    lb = lower_bound(p1.to_numpy(float), 0.05, rng)
    years = p1.groupby(pd.to_datetime(p1.index).year).sum()
    recent = p1[pd.to_datetime(p1.index) >= "2025-01-01"]
    checks = {"lower95>0": bool(lb > 0), "2x_cost_mean>0": bool(p2.mean() > 0), "years_pos>=6": bool((years > 0).sum() >= 6)}
    verdict = "PASS" if all(checks.values()) else "FAIL"
    print(f"\nPORTFOLIO (12 unseen) {p1.index.min()} .. {p1.index.max()} ({len(p1)} days)")
    print(f"mean daily net {p1.mean():.4%}  lower95 {lb:.4%}  Sharpe {sharpe(p1):.2f}  ann. {p1.mean() * 252:.1%}  "
          f"2x-cost mean {p2.mean():.4%}  2025+ mean {recent.mean():.4%}")
    print("by year:", {int(y): f"{v:.1%}" for y, v in years.items()})
    print("checks:", checks, f"\nVERDICT: {verdict}")

    per = {}
    print("\nPER TICKER (descriptive)")
    for s, t in trades.items():
        net = net_returns(t, COSTS[s]).groupby(t.session).sum()
        gross = (t.position * t.label)[t.position != 0]
        per[s] = {"trades": int((t.position != 0).sum()), "gross_bp": float(gross.mean() * 1e4),
                  "mean_daily": float(net.mean()), "sharpe": sharpe(net)}
        print(f"{s:6s} trades {per[s]['trades']:5d} gross/trade {per[s]['gross_bp']:+6.1f} bp "
              f"daily net {net.mean():+.4%} Sharpe {sharpe(net):+.2f}")

    orig = {s: pd.read_parquet(ORIGINAL_OOS / f"oos_{s}.parquet") for s in ORIGINAL}
    p18 = daily_portfolio(trades | orig, COSTS, 1.0)
    print(f"\nSECONDARY: 18-name portfolio (12 unseen + original 6) Sharpe {sharpe(p18):.2f} "
          f"ann. {p18.mean() * 252:.1%}")

    summary = {"verdict": verdict, "checks": checks, "mean_daily": float(p1.mean()), "lower95": lb,
               "sharpe": sharpe(p1), "recent_mean": float(recent.mean()),
               "years": {int(y): float(v) for y, v in years.items()}, "per_ticker": per,
               "secondary": {"p18_sharpe": sharpe(p18), "p18_ann": float(p18.mean() * 252)}}
    (OUT / "result.json").write_text(json.dumps(summary, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="megacap-oos-2026-10-30", kind="megacap-oos",
                                  params={"prereg": "docs/findings/prereg_megacap_oos_2026-10-30.md"},
                                  metrics=json.loads(json.dumps(summary, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
