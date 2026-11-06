"""The daily per-stock model on AAOI (prereg_aaoi_daily_2026-11-06). Same frozen code as megacap_oos_study.

    python scripts/aaoi_daily_study.py     # run once
"""
from __future__ import annotations

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
from sgt.megacap.walkforward import daily_portfolio, walk_forward
from sgt.ops.model_tape import stats

SYM, COST, COST_LOW = "AAOI", 0.0010, 0.0004
OUT = Path("reports/aaoi_daily")


def main() -> int:
    store = ParquetBarStore("data/alpaca")
    spy_b, qqq_b = store.read_frame("SPY", START, END), store.read_frame("QQQ", START, END)
    spy_d, qqq_d = adjusted_daily("SPY"), adjusted_daily("QQQ")
    path = CACHE / f"dfeat_{SYM}.parquet"
    if not path.exists():
        build_daily(store.read_frame(SYM, START, END), adjusted_daily(SYM), spy_b, spy_d, qqq_b, qqq_d).to_parquet(path)
    d = pd.read_parquet(path)
    t = walk_forward(d, WF_START, features=list(DAILY_FEATURES), params=PARAMS, embargo_sessions=1)
    OUT.mkdir(parents=True, exist_ok=True)
    t.to_parquet(OUT / f"oos_{SYM}.parquet")
    rng = np.random.default_rng(SEED)
    p1 = daily_portfolio({SYM: t}, {SYM: COST}, 1.0)
    p2 = daily_portfolio({SYM: t}, {SYM: COST}, 2.0)
    years = p1.groupby(pd.to_datetime(p1.index).year).sum()
    lb = lower_bound(p1.to_numpy(float), 0.05, rng)
    checks = {"lower95>0": bool(lb > 0), "2x_cost_mean>0": bool(p2.mean() > 0), "years_pos>=6": bool((years > 0).sum() >= 6)}
    s = stats(t)
    res = {"verdict": "PASS" if all(checks.values()) else "FAIL", "checks": checks, "mean_daily": float(p1.mean()),
           "lower95": float(lb), "sharpe": float(sharpe(p1)), "years": {int(k): float(v) for k, v in years.items()},
           "mean_daily_cost_0.04%": float(daily_portfolio({SYM: t}, {SYM: COST_LOW}, 1.0).mean()), "tape_stats": s}
    print(f"{SYM}: mean daily {res['mean_daily']:+.4%} lower95 {lb:+.4%} Sharpe {res['sharpe']:.2f} | "
          f"model {s['model_ann']:+.1%}/yr vs hold {s['hold_ann']:+.1%}/yr, hold Sharpe {s['hold_sharpe']:.2f}, "
          f"hit {s['hit']:.0%}, in market {s['traded_share']:.0%}, last 3 mo {s['last63_model']:+.1%} vs "
          f"{s['last63_hold']:+.1%}")
    print("  years:", {k: f"{v:+.1%}" for k, v in res["years"].items()})
    print(f"  checks {checks} -> VERDICT {res['verdict']}")
    (OUT / "result.json").write_text(json.dumps(res, indent=2, default=str))
    init_db()
    with get_session() as sess:
        trial_ledger.record_trial(sess, run_id="aaoi-daily-2026-11-06", kind="aaoi-daily",
                                  params={"prereg": "docs/findings/prereg_aaoi_daily_2026-11-06.md"},
                                  metrics=json.loads(json.dumps(res, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
