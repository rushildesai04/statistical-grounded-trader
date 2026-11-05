"""Fade brain v2 (prereg_fade_brain_2026-11-05): learn bet size from tape signals, news and SEC filings.

    python scripts/fade_brain_study.py dev     # monthly walk-forward on training events (run ONCE)

Events, nets, news and filing flags come from reports/fade_filings/*_events.csv (the champion's net, point-in-time
flags); v1 features and tape signals are recomputed from the SIP bar store.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from sgt.db.session import get_session, init_db
from sgt.decision import trial_ledger
from sgt.fast import meta
from sgt.intraday.store import ParquetBarStore

PREREG = "docs/findings/prereg_fade_brain_2026-11-05.md"
SRC = {"dev": Path("reports/fade_filings/dev_events.csv"), "holdout": Path("reports/fade_filings/holdout_events.csv")}
OUT = Path("reports/fade_brain")
WF_START, MIN_MONTHS = "2025-07", 6
SEED, N_BOOT = 20261105, 10_000


def events(name: str, store: ParquetBarStore) -> pd.DataFrame:
    ev = pd.read_csv(SRC[name]).dropna(subset=["net"]).reset_index(drop=True)
    ev["posted_at"] = pd.to_datetime(ev.posted_at, utc=True)
    return ev.join(meta.build(ev, store)).join(meta.signal_features(ev, store)).join(meta.news_features(ev))


def walk_forward(ev: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Out-of-sample prediction and size weight per event, monthly expanding."""
    months = pd.to_datetime(ev.session).dt.to_period("M")
    parts = []
    for m in sorted(months.unique()):
        if m < pd.Period(WF_START, "M"):
            continue
        tr, te = ev[months < m], ev[months == m]
        if tr.session.nunique() < 20 or months[months < m].nunique() < MIN_MONTHS or te.empty:
            continue
        model = meta.fit_cols(tr, tr.net.to_numpy(float), cols)
        ins, p = model.predict(tr[cols]), model.predict(te[cols])
        parts.append(pd.DataFrame({"pred": p, "w": meta.size_weight(p, ins)}, index=te.index))
    return ev.join(pd.concat(parts), how="inner")


def evaluate(wf: pd.DataFrame, rng) -> dict:
    eq = wf.net.groupby(wf.session).sum()
    wt = (wf.net * wf.w).groupby(wf.session).sum()
    diff = (wt - eq).to_numpy(float)
    lower = float(np.quantile(diff[rng.integers(0, len(diff), size=(N_BOOT, len(diff)))].mean(1), 0.05))
    terc = wf.groupby("w").net.agg(["size", "mean"]).rename(columns={"size": "n"})
    return {"events": len(wf), "sessions": int(eq.size), "ic": float(wf.pred.corr(wf.net, method="spearman")),
            "equal_per_session": float(eq.mean()), "weighted_per_session": float(wt.mean()),
            "diff_per_session": float(diff.mean()), "lower95": lower, "mean_weight": float(wf.w.mean()),
            "by_weight": {str(k): {"n": int(v.n), "mean": float(v["mean"])} for k, v in terc.iterrows()}}


def _print(name: str, r: dict) -> None:
    print(f"{name}: {r['events']} events, {r['sessions']} sessions, IC {r['ic']:+.3f}, mean weight {r['mean_weight']:.2f}")
    print(f"  net/session equal {r['equal_per_session']:+.3%} sized {r['weighted_per_session']:+.3%} "
          f"diff {r['diff_per_session']:+.3%} (lower95 {r['lower95']:+.3%})")
    print("  by weight: " + "  ".join(f"{k}x n{v['n']} {v['mean']:+.2%}" for k, v in r["by_weight"].items()))


def cmd_dev() -> int:
    store = ParquetBarStore("data/alpaca")
    ev = events("dev", store)
    rng = np.random.default_rng(SEED)
    v2 = evaluate(walk_forward(ev, meta.BRAIN_FEATURES), rng)
    v1 = evaluate(walk_forward(ev, meta.FEATURES), rng)
    ok = v2["lower95"] > 0 and 0.9 <= v2["mean_weight"] <= 1.1
    _print("DEV v2 (all signals + news + SEC) — decides", v2)
    _print("SECONDARY v1 features only, same sizing", v1)
    full = meta.fit_cols(ev, ev.net.to_numpy(float), meta.BRAIN_FEATURES)
    imp = sorted(zip(meta.BRAIN_FEATURES, full.feature_importances_.tolist()), key=lambda x: -x[1])[:12]
    print("  top features (gain splits): " + ", ".join(f"{k} {v}" for k, v in imp))
    verdict = "PASS" if ok else "FAIL"
    print(f"VERDICT (dev gate): {verdict}")
    OUT.mkdir(parents=True, exist_ok=True)
    res = {"verdict": verdict, "v2": v2, "v1_features_secondary": v1, "importance": imp}
    (OUT / "dev.json").write_text(json.dumps(res, indent=2))
    init_db()
    with get_session() as s:
        trial_ledger.record_trial(s, run_id="fade-brain-2026-11-05-dev", kind="fade-brain-dev",
                                  params={"prereg": PREREG}, metrics=res)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["dev"])
    return {"dev": cmd_dev}[ap.parse_args().cmd]()


if __name__ == "__main__":
    raise SystemExit(main())
