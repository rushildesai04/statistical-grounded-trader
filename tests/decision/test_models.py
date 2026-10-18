"""DecisionModel tests (DECISION_MODEL.md §6, §10 F4): recovery, tiers, stability,
save/load, inference budget, b(x) table."""
from __future__ import annotations

import time

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from sgt.decision.models import (
    DecisionModel,
    TIER_FULL,
    TIER_MEDIUM,
    TIER_SMALL,
    effective_n,
    select_tier,
)

SIG_LABEL = "y_net__tp2.0_sl1.0_h12"


def _planted(n_sessions=50, n_symbols=5, per=8, seed=0, signal=0.02, n_noise=1):
    rng = np.random.default_rng(seed)
    rows = []
    for d in range(n_sessions):
        sess = f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}"
        for s in (f"S{i}" for i in range(n_symbols)):
            for _ in range(per):
                f0 = float(rng.normal())
                row = {"symbol": s, "session_date": sess, "f_sig": f0,
                       "cost_rt_measured": 0.004, "uniq_w": 1.0}
                for j in range(n_noise):
                    row[f"f_noise{j}"] = float(rng.normal())
                row["f_hasnan"] = np.nan if rng.uniform() < 0.1 else float(rng.normal())
                row[SIG_LABEL] = signal * f0 - 0.004 + float(rng.normal(scale=0.01))
                rows.append(row)
    return pd.DataFrame(rows)


def _feats(df):
    return [c for c in df.columns if c.startswith("f_")]


def test_tier_selection():
    assert select_tier(100).name == TIER_SMALL and not select_tier(100).use_gbm
    assert select_tier(500).name == TIER_MEDIUM and select_tier(500).use_gbm
    assert select_tier(5000).name == TIER_FULL and select_tier(5000).max_features is None


def test_effective_n_counts_clusters():
    df = _planted(n_sessions=10, n_symbols=4, per=3)
    assert effective_n(df) == 40


def test_recovers_planted_signal():
    tr = _planted(seed=1)
    te = _planted(seed=2)
    m = DecisionModel().fit(tr, _feats(tr), SIG_LABEL, weight_col="uniq_w")
    preds = m.predict_frame(te)
    realized = te[SIG_LABEL].to_numpy()
    assert np.corrcoef(preds["mu"], realized)[0, 1] > 0.5
    assert roc_auc_score((realized > 0).astype(int), preds["p"]) > 0.7


def test_stability_selection_small_tier():
    # <300 clusters -> ridge-only, <=5 features; the signal feature must survive.
    df = _planted(n_sessions=40, n_symbols=5, per=6, n_noise=9, seed=3)  # 200 clusters
    m = DecisionModel().fit(df, _feats(df), SIG_LABEL)
    assert m.tier.name == TIER_SMALL
    assert len(m.selected_features) <= 5
    assert "f_sig" in m.selected_features


def test_prediction_fields_and_b_table():
    df = _planted(seed=4)
    m = DecisionModel().fit(df, _feats(df), SIG_LABEL)
    feats = {f: 0.5 for f in m.selected_features}
    pred = m.predict(feats)
    assert pred.sigma >= 0.0
    assert 0.0 <= pred.p <= 1.0
    assert pred.b > 0.0
    assert pred.contributions and all(len(c) == 2 for c in pred.contributions)


def test_save_load_roundtrip(tmp_path):
    df = _planted(seed=5)
    m = DecisionModel().fit(df, _feats(df), SIG_LABEL, config_hash="abc")
    path = m.save(tmp_path / "model.joblib")
    assert path.with_suffix(".joblib.meta.json").exists()
    loaded = DecisionModel.load(path)
    feats = {f: 0.3 for f in m.selected_features}
    a, b = m.predict(feats), loaded.predict(feats)
    assert a.mu == pytest.approx(b.mu) and a.p == pytest.approx(b.p)
    assert loaded.metadata["config_hash"] == "abc"


def test_inference_under_2ms():
    df = _planted(seed=6)
    m = DecisionModel().fit(df, _feats(df), SIG_LABEL)
    feats = {f: 0.1 for f in m.selected_features}
    m.predict(feats)  # warm up
    t0 = time.perf_counter()
    for _ in range(200):
        m.predict(feats)
    median_ms = (time.perf_counter() - t0) / 200 * 1e3
    assert median_ms < 2.0, f"inference {median_ms:.3f} ms exceeds 2 ms"


def test_missing_feature_raises():
    df = _planted(seed=7)
    m = DecisionModel().fit(df, _feats(df), SIG_LABEL)
    with pytest.raises(ValueError):
        m.predict({})
