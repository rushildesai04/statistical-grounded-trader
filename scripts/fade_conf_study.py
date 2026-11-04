"""Calibrated confidence score for fade trades (prereg_fade_confidence_2026-11-04): develop, freeze.

    python scripts/fade_conf_study.py dev      # nested walk-forward on training events, gates G1-G3
    python scripts/fade_conf_study.py freeze   # fit on all training events; save classifier + calibrators
"""
from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from fade_meta_study import events
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast import meta
from sgt.intraday.store import ParquetBarStore

OUT = Path("reports/fade_conf")
MODEL = Path("config/models/fade_conf_v1.txt")
CALIB = Path("config/models/fade_conf_v1_calib.pkl")
WF_START, MIN_MONTHS, CAL_MONTHS = "2025-07", 6, 3
SEED, N_BOOT = 20261104, 10_000


def fit_clf(x: pd.DataFrame, win: np.ndarray):
    import lightgbm as lgb

    return lgb.LGBMClassifier(**meta.PARAMS).fit(x[meta.FEATURES], win.astype(int))


def calibrators(raw: np.ndarray, win: np.ndarray, net: np.ndarray) -> tuple[IsotonicRegression, IsotonicRegression]:
    p = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip").fit(raw, win.astype(float))
    ev = IsotonicRegression(out_of_bounds="clip").fit(raw, net)
    return p, ev


def ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    idx = np.minimum((p * bins).astype(int), bins - 1)
    return float(sum(abs(p[idx == b].mean() - y[idx == b].mean()) * (idx == b).mean()
                     for b in range(bins) if (idx == b).any()))


def session_bound(net: pd.Series, sessions: pd.Series, q: float, rng) -> float:
    g = net.groupby(sessions).agg(["sum", "count"])
    s, c = g["sum"].to_numpy(float), g["count"].to_numpy(float)
    idx = rng.integers(0, len(g), size=(N_BOOT, len(g)))
    return float(np.quantile(s[idx].sum(1) / c[idx].sum(1), q))


def cmd_dev() -> int:
    ev = events("train", ParquetBarStore("data/alpaca"))
    win = (ev.net > 0).to_numpy()
    months = pd.to_datetime(ev.session).dt.to_period("M")
    raw = pd.Series(np.nan, index=ev.index)
    for m in sorted(months.unique()):                       # pass 1: out-of-sample raw scores
        if m < pd.Period(WF_START, "M"):
            continue
        tr, te = ev[months < m], ev[months == m]
        if months[months < m].nunique() < MIN_MONTHS or te.empty:
            continue
        raw[te.index] = fit_clf(tr, win[months < m]).predict_proba(te[meta.FEATURES])[:, 1]
    rows = []
    oos_months = sorted(months[raw.notna()].unique())
    for i, m in enumerate(oos_months):                      # pass 2: calibrate on EARLIER out-of-sample months only
        if i < CAL_MONTHS:
            continue
        past = raw.notna() & (months < m)
        cp, cev = calibrators(raw[past].to_numpy(), win[past.to_numpy()], ev.net[past].to_numpy())
        te = months == m
        rows.append(pd.DataFrame({"p": cp.predict(raw[te].to_numpy()), "ev": cev.predict(raw[te].to_numpy())},
                                 index=ev.index[te]))
    sc = ev.join(pd.concat(rows), how="inner")
    y = (sc.net > 0).to_numpy(float)
    base = y.mean()
    brier, brier0 = float(((sc.p - y) ** 2).mean()), float(((base - y) ** 2).mean())
    e = ece(sc.p.to_numpy(), y)
    rng = np.random.default_rng(SEED)
    band = pd.cut(sc.p * 100, [-1, 30, 50, 70, 101], right=False, labels=["<30", "30-50", "50-70", ">=70"])
    table = {}
    for b, g in sc.groupby(band, observed=False):
        table[str(b)] = {"n": len(g), "share": len(g) / len(sc), "mean_score": float(g.p.mean() * 100) if len(g) else None,
                         "win": float((g.net > 0).mean()) if len(g) else None,
                         "mean_net": float(g.net.mean()) if len(g) else None}
    low, high = sc[band == "<30"], sc[band == ">=70"]
    g1 = e <= 0.05 and brier < brier0
    g2 = g1 and len(low) >= 50 and low.net.mean() < 0 and session_bound(low.net, low.session, 0.95, rng) < 0
    g3 = g1 and len(high) >= 30 and high.net.mean() > 0
    print(f"DEV calibrated {sc.session.min()} .. {sc.session.max()}: {len(sc)} trades, base win {base:.1%}")
    print(f"Brier {brier:.4f} vs base-rate {brier0:.4f} (skill {1 - brier / brier0:+.2%}), ECE {e:.3f}")
    for k, v in table.items():
        print(f"  score {k:>5s}: n {v['n']:4d} ({v['share']:.0%})" + (
            f" mean score {v['mean_score']:.0f} actual win {v['win']:.0%} mean net {v['mean_net']:+.2%}" if v["n"] else ""))
    print(f"G1 calibrated score shown: {'ON' if g1 else 'OFF'} | G2 floor <30: {'ON' if g2 else 'OFF'} | "
          f"G3 auto-approve >=70: {'ON' if g3 else 'OFF'}")
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"g1": g1, "g2": g2, "g3": g3, "brier": brier, "brier_base": brier0, "ece": e, "bands": table, "n": len(sc)}
    (OUT / "dev.json").write_text(json.dumps(res, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-confidence-2026-11-04-dev", kind="fade-confidence-dev",
                                  params={"prereg": "docs/findings/prereg_fade_confidence_2026-11-04.md"}, metrics=res)
    return 0


def cmd_freeze() -> int:
    dev = json.loads((OUT / "dev.json").read_text())
    if not dev["g1"]:
        raise SystemExit("G1 failed: the score is not calibrated; nothing is frozen or shown")
    ev = events("train", ParquetBarStore("data/alpaca"))
    win = (ev.net > 0).to_numpy()
    months = pd.to_datetime(ev.session).dt.to_period("M")
    # calibrators from out-of-sample scores of the walk-forward (never in-sample), classifier on everything
    raw = pd.Series(np.nan, index=ev.index)
    for m in sorted(months.unique()):
        if m < pd.Period(WF_START, "M") or months[months < m].nunique() < MIN_MONTHS:
            continue
        raw[months == m] = fit_clf(ev[months < m], win[months < m]).predict_proba(ev[months == m][meta.FEATURES])[:, 1]
    ok = raw.notna()
    cp, cev = calibrators(raw[ok].to_numpy(), win[ok.to_numpy()], ev.net[ok].to_numpy())
    clf = fit_clf(ev, win)
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    clf.booster_.save_model(str(MODEL))
    CALIB.write_bytes(pickle.dumps({"p": cp, "ev": cev, "g2": dev["g2"], "g3": dev["g3"]}))
    print(f"frozen {MODEL} sha256 {meta.sha256(MODEL)}; calibrators {CALIB} sha256 {meta.sha256(CALIB)}")
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-confidence-v1", kind="fade-confidence-freeze",
                                  params={"model_sha256": meta.sha256(MODEL), "calib_sha256": meta.sha256(CALIB)},
                                  metrics={"g2": dev["g2"], "g3": dev["g3"]})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev", "freeze"])
    return {"dev": cmd_dev, "freeze": cmd_freeze}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
