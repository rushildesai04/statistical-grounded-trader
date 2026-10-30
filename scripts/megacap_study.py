"""Pre-registered per-ticker mega-cap learning models: build, walk forward, score ONCE.

Rules: docs/findings/prereg_megacap_brains_2026-10-30.md (incl. amendment A1).

Usage:
    python scripts/megacap_study.py --dry-run     # adjusted daily + features, row counts only
    python scripts/megacap_study.py               # the one scored run
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from sgt.config import get_settings
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.sources import _alpaca_headers
from sgt.intraday.store import ParquetBarStore
from sgt.megacap.features import FEATURES, build
from sgt.megacap.walkforward import daily_portfolio, net_returns, walk_forward

TICKERS = ["AAPL", "AMZN", "GOOGL", "MSFT", "META", "NVDA", "TSLA", "RDDT"]
COSTS = {t: 0.0004 for t in TICKERS} | {"RDDT": 0.0010}
START, END = date(2016, 1, 4), date(2026, 10, 29)
WF_START = "2018-01"
SEED, N_BOOT = 20261030, 10_000
CACHE = Path("data/megacap")
OUT = Path("reports/megacap")


def adjusted_daily(sym: str) -> pd.DataFrame:
    path = CACHE / f"daily_{sym}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    url = get_settings().alpaca_data_url.rstrip("/") + "/v2/stocks/bars"
    params = {"symbols": sym, "timeframe": "1Day", "start": "2015-11-01", "end": END.isoformat(),
              "adjustment": "all", "feed": "sip", "limit": 10000}
    rows, token = [], None
    for _ in range(20):
        if token:
            params["page_token"] = token
        r = httpx.get(url, params=params, headers=_alpaca_headers(), timeout=30.0)
        r.raise_for_status()
        j = r.json()
        rows += (j.get("bars") or {}).get(sym) or []
        token = j.get("next_page_token")
        if not token:
            break
    else:
        raise SystemExit(f"{sym}: daily pagination did not finish")
    d = pd.DataFrame(rows)
    d.index = pd.to_datetime(d.t).dt.tz_convert("America/New_York").dt.date
    d = d.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close"})[["open", "high", "low", "close"]]
    d.to_parquet(path)
    return d


def features(sym: str, store: ParquetBarStore, spy: pd.DataFrame, qqq: pd.DataFrame) -> pd.DataFrame:
    path = CACHE / f"features_{sym}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    bars = store.read_frame(sym, START, END)
    if bars.empty:
        raise SystemExit(f"{sym}: no 1-minute bars in the store")
    df = build(bars, adjusted_daily(sym), spy, qqq)
    df.to_parquet(path)
    return df


def lower_bound(x: np.ndarray, alpha: float, rng: np.random.Generator) -> float:
    idx = rng.integers(0, len(x), size=(N_BOOT, len(x)))
    return float(np.quantile(x[idx].mean(axis=1), alpha))


def sharpe(p: pd.Series) -> float:
    return float(p.mean() / p.std(ddof=1) * np.sqrt(252)) if p.std(ddof=1) > 0 else float("nan")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    CACHE.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    store = ParquetBarStore("data/alpaca")
    spy, qqq = store.read_frame("SPY", START, END), store.read_frame("QQQ", START, END)
    data = {s: features(s, store, spy, qqq) for s in TICKERS}
    for s, d in data.items():
        print(f"{s:6s} rows {len(d):6d} sessions {d.session.nunique():5d} {d.session.min()} .. {d.session.max()} "
              f"feature NaN share {d[FEATURES].isna().mean().mean():.1%}")
    if args.dry_run:
        return 0

    trades = {s: walk_forward(d, WF_START) for s, d in data.items()}
    for s, t in trades.items():
        t.to_parquet(OUT / f"oos_{s}.parquet")
    rng = np.random.default_rng(SEED)
    p1 = daily_portfolio(trades, COSTS, 1.0)
    p2 = daily_portfolio(trades, COSTS, 2.0)
    lb = lower_bound(p1.to_numpy(float), 0.05, rng)
    years = p1.groupby(pd.to_datetime(p1.index).year).sum()
    recent = p1[pd.to_datetime(p1.index) >= "2025-01-01"]
    checks = {
        "lower95>0": lb > 0, "sharpe>=1": sharpe(p1) >= 1.0, "2x_cost_mean>0": p2.mean() > 0,
        "years_pos>=6": int((years > 0).sum()) >= 6, "2025+_mean>0": recent.mean() > 0,
    }
    verdict = "PASS" if all(checks.values()) else "FAIL"
    print(f"\nPORTFOLIO {p1.index.min()} .. {p1.index.max()} ({len(p1)} days)")
    print(f"mean daily net {p1.mean():.4%}  lower95 {lb:.4%}  Sharpe {sharpe(p1):.2f}  "
          f"ann. {p1.mean() * 252:.1%}  2x-cost mean {p2.mean():.4%}  2025+ mean {recent.mean():.4%}")
    print("by year (sum of daily net):", {int(y): f"{v:.1%}" for y, v in years.items()})
    print("checks:", checks)
    print(f"VERDICT: {verdict}")

    print("\nPER TICKER (Bonferroni alpha 0.05/8; only matters if the portfolio passes)")
    per = {}
    for s, t in trades.items():
        if t.empty:
            continue
        net = net_returns(t, COSTS[s])
        d = net.groupby(t.session).sum()
        tl = lower_bound(d.to_numpy(float), 0.05 / len(TICKERS), rng)
        ic = t.groupby(pd.to_datetime(t.session).dt.year).apply(lambda g: g.pred.corr(g.label, method="spearman"))
        gross = (t.position * t.label)[t.position != 0]
        per[s] = {"days": len(d), "mean_daily": float(d.mean()), "lower": tl, "sharpe": sharpe(d),
                  "trades": int((t.position != 0).sum()), "gross_per_trade": float(gross.mean()),
                  "hit": float((gross > 0).mean()), "ic_mean": float(ic.mean())}
        print(f"{s:6s} days {len(d):5d} trades {per[s]['trades']:6d} gross/trade {gross.mean():+.4%} "
              f"hit {per[s]['hit']:.0%} daily net {d.mean():+.4%} lower {tl:+.4%} Sharpe {sharpe(d):+.2f} "
              f"IC {ic.mean():+.3f}  continue={'yes' if tl > 0 and verdict == 'PASS' else 'no'}")

    print("\nSECONDARY (not used for the decision)")
    half = daily_portfolio(trades, COSTS, 0.5)
    lo = {s: t.assign(position=t.position.clip(lower=0)) for s, t in trades.items()}
    long_only = daily_portfolio(lo, COSTS, 1.0)
    print(f"0.5x costs: mean {half.mean():.4%} Sharpe {sharpe(half):.2f} | long-only: mean "
          f"{long_only.mean():.4%} Sharpe {sharpe(long_only):.2f}")
    allt = pd.concat([t.assign(sym=s) for s, t in trades.items()])
    by_k = allt[allt.position != 0].groupby("k").apply(lambda g: (g.position * g.label).mean())
    print("gross per trade by decision minute:", {int(k): f"{v:+.4%}" for k, v in by_k.items()})
    pooled_in = pd.concat([d.assign(tid=i) for i, (s, d) in enumerate(data.items())], ignore_index=True)
    pooled = walk_forward(pooled_in, WF_START, features=list(FEATURES) + ["tid"])
    pooled_trades = {s: pooled[pooled.tid == i] for i, s in enumerate(data)}
    pp = daily_portfolio(pooled_trades, COSTS, 1.0)
    print(f"pooled single model: mean {pp.mean():.4%} Sharpe {sharpe(pp):.2f}")

    summary = {"verdict": verdict, "checks": checks, "mean_daily": float(p1.mean()), "lower95": lb,
               "sharpe": sharpe(p1), "years": {int(y): float(v) for y, v in years.items()}, "per_ticker": per,
               "secondary": {"half_cost_sharpe": sharpe(half), "long_only_sharpe": sharpe(long_only),
                             "pooled_sharpe": sharpe(pp)}}
    (OUT / "result.json").write_text(json.dumps(summary, indent=2, default=str))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="megacap-brains-2026-10-30", kind="megacap-brains",
                                  params={"prereg": "docs/findings/prereg_megacap_brains_2026-10-30.md"},
                                  metrics=json.loads(json.dumps(summary, default=str)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
