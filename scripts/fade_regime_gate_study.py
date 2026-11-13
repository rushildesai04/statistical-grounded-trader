"""Regime gate for the alert fade (docs/findings/prereg_fade_regime_gate_2026-11-13.md).

    .venv/bin/python scripts/fade_regime_gate_study.py      # run ONCE (frozen prereg)

Stand aside on a session when the fade's own record over the previous sessions is poor. Report only."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("reports/fade_regime_gate")
TRAIN, HOLD, FWD = "reports/fade_news/dev_events.csv", "reports/alert_fade/trades.csv", "reports/alert_fade/forward.csv"
LOOKBACK, MIN_TRADES, WIN_MIN = 5, 15, 0.50
ALPHA, MIN_SKIP_TRAIN, MIN_SKIP_HOLD = 0.05 / 2, 100, 30
SEED, N_BOOT = 20261113, 10_000
GATES = ("G1", "G2")


def load() -> pd.DataFrame:
    parts = [pd.read_csv(p)[["symbol", "session", "net"]].assign(split=s)
             for p, s in ((TRAIN, "train"), (HOLD, "holdout"), (FWD, "forward"))]
    t = pd.concat(parts, ignore_index=True)
    t["session"] = t.session.astype(str)
    return t.sort_values("session", kind="stable").reset_index(drop=True)


def skipped_sessions(t: pd.DataFrame, lookback: int = LOOKBACK) -> dict[str, set[str]]:
    """Sessions each gate stands aside on, decided from the ``lookback`` earlier fade sessions only."""
    by = t.groupby("session").net.agg(n="size", s="sum", w=lambda x: int((x > 0).sum()))
    out: dict[str, set[str]] = {g: set() for g in GATES}
    for k, d in enumerate(by.index):
        prev = by.iloc[max(0, k - lookback):k]
        n = int(prev.n.sum())
        if len(prev) < lookback or n < MIN_TRADES:
            continue                                   # too little record: the gate is open
        if prev.w.sum() / n < WIN_MIN:
            out["G1"].add(d)
        if prev.s.sum() / n < 0:
            out["G2"].add(d)
    return out


def _boot(sessions: np.ndarray, vals: np.ndarray) -> np.ndarray:
    u, inv = np.unique(sessions, return_inverse=True)
    s, n = np.bincount(inv, vals, len(u)), np.bincount(inv, minlength=len(u)).astype(float)
    idx = np.random.default_rng(SEED).integers(0, len(u), size=(N_BOOT, len(u)))
    with np.errstate(invalid="ignore", divide="ignore"):
        return s[idx].sum(1) / n[idx].sum(1)


def stats(t: pd.DataFrame, skip: set[str]) -> dict:
    """Statistic = mean(gated - ungated) = -(summed net of skipped trades) / all trades."""
    if t.empty:
        return {"n": 0}
    sk = t.session.isin(skip).to_numpy()
    diff = np.where(sk, -t.net.to_numpy(float), 0.0)
    m = _boot(t.session.to_numpy(), diff)
    kept, skp = t[~sk], t[sk]
    return {"n": int(len(t)), "n_skipped": int(sk.sum()), "sessions_skipped": int(len(set(t.session) & skip)),
            "sessions": int(t.session.nunique()), "stat": float(diff.mean()),
            "p": float(((m <= 0) | np.isnan(m)).mean()), "ungated_mean": float(t.net.mean()),
            "gated_mean": float(np.where(sk, 0.0, t.net).mean()),
            "kept_mean": float(kept.net.mean()) if len(kept) else None,
            "kept_win": float((kept.net > 0).mean()) if len(kept) else None,
            "skipped_mean": float(skp.net.mean()) if len(skp) else None,
            "skipped_win": float((skp.net > 0).mean()) if len(skp) else None}


def train_pass(s: dict) -> bool:
    return bool(s.get("n_skipped", 0) >= MIN_SKIP_TRAIN and s.get("stat", 0.0) > 0 and s.get("p", 1.0) < ALPHA)


def main() -> int:
    t = load()
    skip = skipped_sessions(t)
    tr, ho, fw = (t[t.split == s] for s in ("train", "holdout", "forward"))
    out = {"prereg": "docs/findings/prereg_fade_regime_gate_2026-11-13.md", "alpha": ALPHA,
           "train": {g: stats(tr, skip[g]) for g in GATES}, "holdout": {}, "adopt": []}
    out["train_pass"] = {g: train_pass(out["train"][g]) for g in GATES}
    for g in GATES:
        if out["train_pass"][g]:                       # held-out opened only for a training pass
            h = out["holdout"][g] = stats(ho, skip[g])
            if h.get("n_skipped", 0) >= MIN_SKIP_HOLD and h.get("stat", 0.0) > 0:
                out["adopt"].append(g)
    sm = tr.groupby("session").net.mean()
    out["secondary"] = {
        "2_lag1_corr_session_mean_train": float(sm.autocorr(1)),
        "3_forward": {g: {**stats(fw, skip[g]), "skipped": sorted(set(fw.session) & skip[g])} for g in GATES},
        "4_lookbacks_train": {str(k): {g: stats(tr, skipped_sessions(t, k)[g]) for g in GATES} for k in (3, 10)}}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "result.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    from sgt.db.session import get_session, init_db
    from sgt.decision import trial_ledger
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-regime-gate-2026-11-13", kind="fade-regime-gate",
                                  params={"prereg": out["prereg"]}, metrics=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
