"""Size each fade by pre-alert 1-minute volatility (prereg_fade_volsize_2026-11-08).

    python scripts/fade_volsize_study.py      # run once: training gate + held-out secondary
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.intraday.store import ParquetBarStore

OUT = Path("reports/fade_volsize")
SEED, N_BOOT, ALPHA, LOOKBACK, MIN_BARS, LO, HI = 20261108, 10_000, 0.05, 30, 10, 0.5, 2.0


def sigma(store: ParquetBarStore, symbol: str, session: str, entry_ts: pd.Timestamp) -> float | None:
    d = date.fromisoformat(session)
    f = store.read_frame(symbol, d, d)
    if f.empty:
        return None
    ts = pd.to_datetime(f.ts, utc=True)
    done = f.loc[ts + pd.Timedelta(minutes=1) <= entry_ts].assign(ts=ts).sort_values("ts").tail(LOOKBACK + 1)
    c = done.close.to_numpy(float)
    c = c[c > 0]
    if len(c) < MIN_BARS + 1:
        return None
    s = float(np.std(np.diff(np.log(c)), ddof=1))
    return s if math.isfinite(s) and s > 0 else None


def events(name: str, store: ParquetBarStore) -> pd.DataFrame:
    e = pd.read_csv(f"reports/fade_news/{name}_events.csv")
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv")[["symbol", "session", "entry_ts", "entry"]]
    e = e.merge(src, on=["symbol", "session"], how="left").dropna(subset=["net", "entry_ts"])
    e["sigma"] = [sigma(store, r.symbol, str(r.session), pd.Timestamp(r.entry_ts)) for r in e.itertuples()]
    return e


def evaluate(e: pd.DataFrame, m: float) -> dict:
    w = np.where(e.sigma.notna(), np.clip(m / e.sigma.fillna(m), LO, HI), 1.0)
    net = e.net.to_numpy(float)
    u, inv = np.unique(e.session.astype(str).to_numpy(), return_inverse=True)
    swn, sw, sn, cnt = (np.bincount(inv, x, len(u)) for x in (w * net, w, net, np.ones_like(net)))
    rng = np.random.default_rng(SEED)
    b = rng.integers(0, len(u), size=(N_BOOT, len(u)))
    diff = swn[b].sum(1) / sw[b].sum(1) - sn[b].sum(1) / cnt[b].sum(1)
    scale = cnt.sum() / sw.sum()                           # same average dollars as equal weight

    def sharpe(x):
        return float(x.mean() / x.std() * math.sqrt(252)) if x.std() > 0 else None
    q = pd.qcut(e.sigma, 5, labels=False, duplicates="drop")
    res = {"n": len(e), "coverage": float(e.sigma.notna().mean()), "mean_w": float(w.mean()),
           "equal": float(net.mean()), "weighted_per_dollar": float((w * net).sum() / w.sum()),
           "diff": float(diff.mean()), "diff_lower": float(np.quantile(diff, ALPHA)),
           "sharpe_equal": sharpe(sn), "sharpe_weighted": sharpe(swn * scale),
           "by_quintile": {int(k): float(v) for k, v in e.groupby(q).net.mean().items()},
           "corr_sigma_price": float(e[["sigma", "entry"]].corr(method="spearman").iloc[0, 1])}
    res["pass"] = bool(res["diff_lower"] > 0)
    return res


def main() -> int:
    store = ParquetBarStore("data/alpaca")
    dev = events("dev", store)
    m = float(dev.sigma.median())
    out = {"m": m, "dev": evaluate(dev, m), "holdout": evaluate(events("holdout", store), m)}
    for k in ("dev", "holdout"):
        r = out[k]
        print(f"{k:7s} n{r['n']} cov {r['coverage']:.0%} equal {r['equal']:+.2%} weighted/$ {r['weighted_per_dollar']:+.2%} "
              f"diff {r['diff']:+.2%} (lower {r['diff_lower']:+.2%}) Sharpe {r['sharpe_equal']:.2f} -> "
              f"{r['sharpe_weighted']:.2f} -> {'PASS' if r['pass'] else 'fail'}{' (decides)' if k == 'dev' else ''}")
        print(f"        by sigma quintile (low->high) {({q: round(v * 100, 2) for q, v in r['by_quintile'].items()})} "
              f"corr(sigma, price) {r['corr_sigma_price']:+.2f}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-volsize-2026-11-08-dev", kind="fade-volsize-dev",
                                  params={"prereg": "docs/findings/prereg_fade_volsize_2026-11-08.md"}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
