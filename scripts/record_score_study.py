"""Record score: skip model calls whose stock-and-side record so far has no expected money
(docs/findings/prereg_record_score_2026-11-14.md).

    .venv/bin/python scripts/record_score_study.py              # training, run ONCE
    .venv/bin/python scripts/record_score_study.py --holdout    # only after a training PASS, once

Report only: nothing here changes a book."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

import money_score_study as ms
from sgt.megacap import score

OUT = Path("reports/record_score")
MIN_SKIPPED = {"train": 100, "holdout": 30}


def calls_scored(t: pd.DataFrame) -> pd.DataFrame:
    c = t[t.position != 0].copy()
    c["net"] = c.position * c.label - c.symbol.map(score.cost)
    c["ev"] = score.record_ev(c)
    return c


def side_test(c: pd.DataFrame, split: str) -> dict:
    """Mean over all calls of (gated - ungated) = -(summed net of skipped calls) / calls."""
    if c.empty:
        return {"calls": 0, "verdict": "NO VERDICT (no calls): FAIL"}
    skip = (c.ev <= 0).to_numpy()
    net = c.net.to_numpy(float)
    kept, gone = c[~skip], c[skip]
    out = {"calls": int(len(c)), "skipped": int(skip.sum()),
           "kept_mean": float(kept.net.mean()) if len(kept) else None,
           "kept_win": float((kept.net > 0).mean()) if len(kept) else None,
           "kept_ev_mean": float(kept.ev.mean()) if len(kept) else None,
           "skipped_mean": float(gone.net.mean()) if len(gone) else None,
           "skipped_win": float((gone.net > 0).mean()) if len(gone) else None,
           "ungated_mean": float(net.mean()),
           "skipped_by_year": {y: int(v) for y, v in gone.groupby(gone.session.str[:4]).size().items()},
           "most_skipped": {s: int(v) for s, v in gone.symbol.value_counts().head(6).items()}}
    if skip.sum() < MIN_SKIPPED[split]:
        return {**out, "verdict": "NO VERDICT (too few skipped): FAIL"}
    stat, lower, p = ms.boot_p(c.session.to_numpy(), np.where(skip, -net, 0.0), np.ones(len(c)))
    return {**out, "gate_adds": stat, "lower": lower, "p": p,
            "verdict": "PASS" if stat > 0 and p < ms.ALPHA else "FAIL"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--holdout", action="store_true")
    a = ap.parse_args()
    window, split = (ms.HOLD, "holdout") if a.holdout else (ms.TRAIN, "train")
    c = calls_scored(ms.load())
    w = c[(c.session >= window[0]) & (c.session <= window[1])]
    res = {"prereg": "docs/findings/prereg_record_score_2026-11-14.md", "window": window, "K": score.K,
           "G-LONG": side_test(w[w.position > 0], split), "G-SHORT": side_test(w[w.position < 0], split)}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"result_{split}.json").write_text(json.dumps(res, indent=1) + "\n")
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
