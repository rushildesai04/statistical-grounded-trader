"""Walk-forward harness must run, stay time-ordered, and always report
baselines. No shuffled split."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from sgt.backtest.harness import run_all, walk_forward
from sgt.features.store import FEATURE_COLUMNS
from sgt.models.baselines import LogisticBaseline, MajorityClass


def _synthetic_frame(n_days: int = 40, per_day: int = 5) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for d in range(n_days):
        as_of = start + timedelta(days=d)
        for _ in range(per_day):
            fwd = float(rng.normal(0, 0.03))
            cls = "up" if fwd > 0.001 else "down" if fwd < -0.001 else "flat"
            row = {"ticker": "AAPL", "as_of_ts": as_of,
                   "fwd_return": fwd, "cls": cls, "vol_bucket": "mid"}
            for c in FEATURE_COLUMNS:
                row[c] = float(rng.normal())
            rows.append(row)
    return pd.DataFrame(rows)


def test_walk_forward_runs():
    df = _synthetic_frame()
    report = walk_forward(df, MajorityClass, n_splits=5)
    s = report.summary()
    assert s["model"] == "majority"
    assert s["n_test"] > 0
    # predicting a single fixed class can never beat the test-fold base rate.
    assert s["edge_vs_base"] <= 1e-9


def test_run_all_reports_baselines_and_gate():
    df = _synthetic_frame()
    report = run_all(df, candidate_factory=LogisticBaseline, n_splits=5)
    assert "majority" in report["models"]
    assert "logistic" in report["models"]
    assert report["promotion"]["candidate"] == "logistic"
    assert set(report["promotion"]) >= {"beats_majority", "beats_logistic", "promote"}


def test_empty_frame_safe():
    report = run_all(pd.DataFrame(), candidate_factory=None, n_splits=5)
    assert report["models"]["majority"]["n_test"] == 0
