"""Learned fade exit (prereg_fade_exit_learn_2026-11-06).

    python scripts/fade_exit_learn_study.py dev      # monthly walk-forward on training events (run ONCE)
    python scripts/fade_exit_learn_study.py freeze   # only after a dev PASS: fit on all training, score held-out

Same events, entries, stop and costs as the frozen rule; the champion here equals alert_fade_test.score.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from alert_fade_test import LOCATE
from fade_challengers import TAIL_PP, _rth, events
from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast import exit_learn as xl
from sgt.fast import meta
from sgt.intraday.store import ParquetBarStore

PREREG = "docs/findings/prereg_fade_exit_learn_2026-11-06.md"
OUT = Path("reports/fade_exit_learn")
MODEL = Path(__file__).resolve().parents[1] / "config" / "models" / "fade_exit_learn_v1.txt"
WF_START, MIN_MONTHS = "2025-07", 6
SEED, N_BOOT = 20261106, 10_000


def build(split: str, store: ParquetBarStore) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per event (champion gross, cost) and per checkpoint (features, label)."""
    ev, rows = [], []
    for i, r in enumerate(events(split).itertuples(index=False)):
        bars = _rth(store, r.symbol, str(r.session))
        champ, cps = xl.checkpoints(bars, pd.Timestamp(r.entry_ts), float(r.entry), str(r.alert_type))
        if champ is None:
            continue
        ev.append({"eid": i, "symbol": r.symbol, "session": str(r.session), "entry": float(r.entry),
                   "champ_gross": champ["gross"], "cost": float(r.cost_rt) + LOCATE})
        rows += [{"eid": i, **c} for c in cps]
    return pd.DataFrame(ev), pd.DataFrame(rows)


def fit(rows: pd.DataFrame):
    import lightgbm as lgb

    y = rows.label.to_numpy(float)
    lo, hi = np.quantile(y, [0.01, 0.99])
    return lgb.LGBMRegressor(**meta.PARAMS).fit(rows[xl.FEATURES], np.clip(y, lo, hi))


def apply(ev: pd.DataFrame, rows: pd.DataFrame, pred: pd.Series, margin: float = 0.0) -> pd.DataFrame:
    """Policy gross per event (events with no checkpoint keep the champion)."""
    out = []
    by = {e: g.sort_values("k") for e, g in rows.assign(pred=pred).groupby("eid")}
    for r in ev.itertuples(index=False):
        g = by.get(r.eid)
        gross, k = (r.champ_gross, None) if g is None else xl.policy_gross(r.entry, r.champ_gross, g, g.pred.to_numpy(),
                                                                            margin)
        out.append({"eid": r.eid, "learn_gross": gross, "cover_k": k})
    return ev.merge(pd.DataFrame(out), on="eid")


def evaluate(t: pd.DataFrame, rng) -> dict:
    t = t.assign(net_c=t.champ_gross - t.cost, net_l=t.learn_gross - t.cost)
    daily = t.groupby("session")[["net_c", "net_l"]].sum()
    diff = (daily.net_l - daily.net_c).to_numpy(float)
    means = diff[rng.integers(0, len(diff), size=(N_BOOT, len(diff)))].mean(1)
    early = t[t.cover_k.notna()]
    p01_c, p01_l = float(np.quantile(t.net_c, 0.01)), float(np.quantile(t.net_l, 0.01))
    return {"events": len(t), "sessions": int(daily.shape[0]), "champ_mean": float(t.net_c.mean()),
            "learn_mean": float(t.net_l.mean()), "diff_per_session": float(diff.mean()),
            "lower95": float(np.quantile(means, 0.05)), "p01_champ": p01_c, "p01_learn": p01_l,
            "covered_early": len(early), "early_share": float(len(early) / len(t)),
            "early_gross": float(early.learn_gross.mean()) if len(early) else None,
            "early_if_held": float(early.champ_gross.mean()) if len(early) else None,
            "by_checkpoint": {str(int(k)): int(n) for k, n in early.cover_k.value_counts().sort_index().items()},
            "pass": bool(np.quantile(means, 0.05) > 0 and p01_l >= p01_c - TAIL_PP)}


def _print(name: str, r: dict) -> None:
    print(f"{name}: {r['events']} events, {r['sessions']} sessions | champion {r['champ_mean']:+.2%}/trade, learned "
          f"{r['learn_mean']:+.2%}/trade | diff/session {r['diff_per_session']:+.3%} (lower95 {r['lower95']:+.3%}) | "
          f"p01 {r['p01_champ']:+.1%} -> {r['p01_learn']:+.1%}")
    if r["covered_early"]:
        print(f"  covered early {r['covered_early']} ({r['early_share']:.0%}) at {r['by_checkpoint']}: "
              f"gross {r['early_gross']:+.2%} vs {r['early_if_held']:+.2%} if held")


def walk_forward(ev: pd.DataFrame, rows: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    months = pd.to_datetime(ev.session).dt.to_period("M")
    m_of = dict(zip(ev.eid, months))
    rm = rows.eid.map(m_of)
    preds, keep = [], []
    for m in sorted(months.unique()):
        if m < pd.Period(WF_START, "M"):
            continue
        tr, te = rows[rm < m], rows[rm == m]
        if months[months < m].nunique() < MIN_MONTHS or tr.empty:
            continue
        keep += ev.eid[months == m].tolist()
        if not te.empty:
            preds.append(pd.Series(fit(tr).predict(te[xl.FEATURES]), index=te.index))
    wf_ev = ev[ev.eid.isin(keep)]
    return wf_ev, pd.concat(preds)


def cmd_dev() -> int:
    store = ParquetBarStore("data/alpaca")
    ev, rows = build("train", store)
    from alert_fade_test import score as research_score

    ref = research_score(events("train"), store).net.to_numpy(float)[ev.eid.to_numpy()]
    if not np.allclose((ev.champ_gross - ev.cost).to_numpy(float), ref, rtol=0, atol=1e-12):
        raise SystemExit("champion does not reproduce alert_fade_test.score: nothing computed")
    OUT.mkdir(parents=True, exist_ok=True)
    rows.to_parquet(OUT / "train_rows.parquet")
    wf_ev, pred = walk_forward(ev, rows)
    wf_rows = rows.loc[pred.index]
    rng = np.random.default_rng(SEED)
    res = evaluate(apply(wf_ev, wf_rows, pred), rng)
    sec = evaluate(apply(wf_ev, wf_rows, pred, xl.MARGIN), np.random.default_rng(SEED))
    _print(f"DEV walk-forward {wf_ev.session.min()} .. {wf_ev.session.max()} (decides)", res)
    _print("SECONDARY margin 0.5%", sec)
    full = fit(rows)
    imp = sorted(zip(xl.FEATURES, full.feature_importances_.tolist()), key=lambda x: -x[1])[:10]
    print("  top features: " + ", ".join(f"{k} {v}" for k, v in imp))
    print(f"  checkpoint rows {len(rows)}; label mean {rows.label.mean():+.3%} (hold vs cover now)")
    verdict = "PASS" if res["pass"] else "FAIL"
    print(f"VERDICT (dev gate): {verdict}")
    out = {"verdict": verdict, "dev": res, "margin_secondary": sec, "importance": imp}
    (OUT / "dev.json").write_text(json.dumps(out, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-exit-learn-2026-11-06-dev", kind="fade-exit-learn-dev",
                                  params={"prereg": PREREG}, metrics=out)
    return 0


def cmd_freeze() -> int:
    dev = json.loads((OUT / "dev.json").read_text())
    if dev.get("verdict") != "PASS":
        raise SystemExit("dev gate did not pass: nothing is frozen (prereg)")
    store = ParquetBarStore("data/alpaca")
    _, rows = build("train", store)
    model = fit(rows)
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    model.booster_.save_model(str(MODEL))
    sha = meta.sha256(MODEL)
    MODEL.with_suffix(".json").write_text(json.dumps({"sha256": sha, "features": xl.FEATURES}, indent=2) + "\n")
    ev, hrows = build("holdout", store)
    pred = pd.Series(model.predict(hrows[xl.FEATURES]), index=hrows.index) if len(hrows) else pd.Series(dtype=float)
    sec = evaluate(apply(ev, hrows, pred), np.random.default_rng(SEED))
    _print("SECONDARY held-out (frozen model; already seen)", sec)
    (OUT / "freeze.json").write_text(json.dumps({"sha256": sha, "holdout_secondary": sec}, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-exit-learn-v1-freeze", kind="fade-exit-learn-freeze",
                                  params={"prereg": PREREG, "model": str(MODEL)}, metrics={"sha256": sha, **sec})
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev", "freeze"])
    return {"dev": cmd_dev, "freeze": cmd_freeze}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
