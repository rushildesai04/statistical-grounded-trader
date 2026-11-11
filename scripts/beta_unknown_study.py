"""Unknown-beta shorts (owner 2026-11-11: "unknown-beta study"). Report only, seen data (the F7 basis events).

Live rule F7 skips the short when beta >= 3, but an UNKNOWN beta (fewer than 60 daily returns in the year before the
alert, or no daily bars) never blocks. Question: do no-news shorts on unknown-beta names lose?

Decision rule, fixed before the run: make unknown beta block the short (fail closed) only if the no-news
unknown-beta bucket averages < 0 per trade AND is worse than no-news beta < 3 at Welch p < 0.05. Otherwise keep.

Beta as the live rule (`sgt.fast.beta`): raw OLS beta of daily returns vs SPY over the prior 252 sessions, at least
60 returns, from daily bars strictly before the alert's session (data/daily, local). CAVEAT (found on the run): the
local daily bars are NOT split-adjusted (25 of 92 'beta >= 3' names had a +200% one-day jump = reverse split), so the
known-beta buckets here are contaminated (>= 3: 168 alerts vs 76 in the F7 basis on adjusted SIP). The UNKNOWN bucket
depends only on history length and matches the F7 basis (622 vs 623 alerts, +0.32% vs +0.28%): the answer holds.
Usage: .venv/bin/python scripts/beta_unknown_study.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from sgt.fast.beta import MIN_OBS, WINDOW
from sgt.risk.sizing import asset_risk

ROOT = Path(__file__).resolve().parents[1]
EVENTS = [ROOT / "reports/fade_news/dev_events.csv", ROOT / "reports/fade_news/holdout_events.csv"]
DAILY = ROOT / "data/daily"
OUT = ROOT / "reports/fade_beta_unknown"


def daily(symbol: str, cache: dict) -> pd.DataFrame | None:
    if symbol not in cache:
        p = DAILY / f"{symbol}.parquet"
        cache[symbol] = (pd.read_parquet(p).assign(date=lambda x: pd.to_datetime(x.date)).set_index("date")
                         .sort_index() if p.exists() else None)
    return cache[symbol]


def beta_at(symbol: str, session: str, mkt: pd.Series, cache: dict) -> tuple[float | None, int, str]:
    """(beta or None, returns used, why unknown: '' | 'no daily file' | 'short history')."""
    f = daily(symbol, cache)
    if f is None:
        return None, 0, "no daily file"
    prior = f[f.index < pd.Timestamp(session)]
    if len(prior) < 2:
        return None, 0, "short history"
    r = asset_risk(symbol, prior, mkt[mkt.index < pd.Timestamp(session)], window=WINDOW)
    if r.n_obs < MIN_OBS or math.isnan(r.raw_beta):
        return None, r.n_obs, "short history"
    return float(r.raw_beta), r.n_obs, ""


def summary(x: pd.Series) -> dict:
    x = x.dropna()
    n = len(x)
    if n == 0:
        return {"n": 0}
    m, se = float(x.mean()), float(x.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    return {"n": n, "mean": round(m, 5), "ci95": [round(m - 1.96 * se, 5), round(m + 1.96 * se, 5)],
            "win": round(float((x > 0).mean()), 3), "stop_rate": round(float((x < -0.2).mean()), 3)}


def main() -> int:
    ev = pd.concat([pd.read_csv(p) for p in EVENTS], ignore_index=True)
    cache: dict = {}
    mkt = daily("SPY", cache)["close"]
    rows = [beta_at(s, d, mkt, cache) for s, d in zip(ev.symbol, ev.session)]
    ev["beta"], ev["n_obs"], ev["why"] = [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]
    ev["bucket"] = np.select([ev.beta.isna(), ev.beta >= 3, ev.beta >= 1.5], ["unknown", ">=3", "1.5-3"], "<1.5")
    nonews = ev[~ev.news_own.astype(bool)]
    out = {"all": {b: summary(g.net) for b, g in ev.groupby("bucket")},
           "no_news": {b: summary(g.net) for b, g in nonews.groupby("bucket")},
           "no_news_unknown_by_reason": {w: summary(g.net) for w, g in nonews[nonews.bucket == "unknown"].groupby("why")}}
    unk = nonews[nonews.bucket == "unknown"].net.dropna()
    known = nonews[nonews.bucket.isin(["<1.5", "1.5-3"])].net.dropna()
    t, p = stats.ttest_ind(unk, known, equal_var=False)
    out["test"] = {"unknown_vs_known_lt3_no_news": {"welch_t": round(float(t), 3), "p": round(float(p), 4)}}
    out["decision"] = ("FAIL CLOSED: block unknown-beta shorts" if unk.mean() < 0 and p < 0.05 and t < 0
                       else "KEEP: unknown beta does not block")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(out, indent=2))
    ev.to_csv(OUT / "events.csv", index=False)
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
