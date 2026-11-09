"""Same-day results (breaker) and alert bursts vs the fade (prereg_fade_dayflow_2026-11-08).

    python scripts/fade_dayflow_study.py      # run once: training gate + held-out secondary
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger

OUT = Path("reports/fade_dayflow")
SEED, N_BOOT, ALPHA, MIN_N, HOLD = 20261108, 10_000, 0.05, 100, pd.Timedelta(minutes=60)


def events(name: str) -> pd.DataFrame:
    e = pd.read_csv(f"reports/fade_news/{name}_events.csv")
    src = pd.read_csv("reports/telegram/trades_pooled_first.csv")[["symbol", "session", "entry_ts"]]
    e = e.merge(src, on=["symbol", "session"], how="left").dropna(subset=["net", "entry_ts"])
    e["entry_ts"] = pd.to_datetime(e.entry_ts, utc=True)
    return e.sort_values(["session", "entry_ts"]).reset_index(drop=True)


def features(e: pd.DataFrame) -> pd.DataFrame:
    """Point in time: known prior results (closed by entry + 60 min) and the 30-minute alert burst."""
    known_losses, last2_lost, burst = [], [], []
    for _, g in e.groupby("session", sort=False):
        ts, net = g.entry_ts.to_numpy(), g.net.to_numpy(float)
        for i in range(len(g)):
            k = [j for j in range(i) if ts[j] + HOLD <= ts[i]]          # results known at this entry
            known_losses.append(sum(net[j] < 0 for j in k))
            last2_lost.append(len(k) >= 2 and net[k[-1]] < 0 and net[k[-2]] < 0)
            burst.append(sum(ts[i] - pd.Timedelta(minutes=30) <= ts[j] < ts[i] for j in range(i)))
    return e.assign(known_losses=known_losses, last2_lost=last2_lost, burst=burst)


def skip_test(t: pd.DataFrame, skip: np.ndarray) -> dict:
    """Mean net of the skipped trades with a session-bootstrap one-sided upper bound (skip helps if < 0)."""
    rng = np.random.default_rng(SEED)
    u, inv = np.unique(t.session.astype(str).to_numpy(), return_inverse=True)
    net = t.net.to_numpy(float)
    s, n = np.bincount(inv, net * skip, len(u)), np.bincount(inv, skip.astype(float), len(u))
    b = rng.integers(0, len(u), size=(N_BOOT, len(u)))
    with np.errstate(invalid="ignore", divide="ignore"):
        boot = s[b].sum(1) / n[b].sum(1)
    res = {"n_skip": int(skip.sum()), "mean_skip": float(net[skip].mean()) if skip.any() else None,
           "mean_keep": float(net[~skip].mean()), "upper": float(np.nanquantile(boot, 1 - ALPHA)) if skip.any() else None,
           "per_session_gain": float(-s.sum() / len(u))}
    res["pass"] = bool(res["n_skip"] >= MIN_N and res["upper"] is not None and res["upper"] < 0)
    return res


def groups(t: pd.DataFrame, col: str, bins: list, labels: list) -> dict:
    g = pd.cut(t[col], bins=bins, labels=labels, right=False)
    return {str(k): {"n": int(len(v)), "mean": float(v.net.mean()) if len(v) else None}
            for k, v in t.groupby(g, observed=False)}


def halves(t: pd.DataFrame) -> float | None:
    rows = []
    for _, g in t.groupby("session"):
        if len(g) >= 4:
            h = len(g) // 2
            rows.append((g.net.iloc[:h].mean(), g.net.iloc[h:].mean()))
    return float(np.corrcoef(np.array(rows).T)[0, 1]) if len(rows) > 10 else None


def main() -> int:
    out = {}
    for name in ("dev", "holdout"):
        t = features(events(name))
        r = {"n": len(t), "breaker": skip_test(t, t.last2_lost.to_numpy(bool)),
             "burst": skip_test(t, (t.burst >= 3).to_numpy()),
             "by_known_losses": groups(t, "known_losses", [0, 1, 2, 999], ["0", "1", "2+"]),
             "by_burst": groups(t, "burst", [0, 1, 3, 999], ["0", "1-2", "3+"]), "halves_corr": halves(t)}
        out[name] = r
        for k in ("breaker", "burst"):
            x = r[k]
            print(f"{name:7s} {k:7s}: skip n{x['n_skip']} mean {x['mean_skip'] or 0:+.2%} (upper {x['upper'] or 0:+.2%}) "
                  f"keep {x['mean_keep']:+.2%} gain/session {x['per_session_gain']:+.3%} -> "
                  f"{'PASS' if x['pass'] else 'fail'}{' (decides)' if name == 'dev' else ''}")
        print(f"{name:7s} by known losses {r['by_known_losses']}")
        print(f"{name:7s} by burst {r['by_burst']}  halves corr {r['halves_corr']}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-dayflow-2026-11-08-dev", kind="fade-dayflow-dev",
                                  params={"prereg": "docs/findings/prereg_fade_dayflow_2026-11-08.md"}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
