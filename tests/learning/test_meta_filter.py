"""Meta-filter tests (plan §9.1): learning, calibration, explainability, budget."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from sklearn.metrics import roc_auc_score

from sgt.learning.meta_filter import GBMMetaFilter, LogisticMetaFilter

FEATURES = ("f1", "f2", "f3")


def _planted(n: int, seed: int) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    f1 = rng.normal(size=n)
    f2 = rng.normal(size=n)
    f3 = rng.normal(size=n)
    lin = 1.6 * f1 - 1.1 * f2 + 0.5 * rng.normal(size=n)
    y = (rng.uniform(size=n) < 1.0 / (1.0 + np.exp(-lin))).astype(int)
    return pd.DataFrame({"f1": f1, "f2": f2, "f3": f3}), y


def _noise(n: int, seed: int) -> tuple[pd.DataFrame, np.ndarray]:
    rng = np.random.default_rng(seed)
    X = pd.DataFrame({c: rng.normal(size=n) for c in FEATURES})
    y = (rng.uniform(size=n) < 0.5).astype(int)
    return X, y


def _scores(model, X: pd.DataFrame) -> np.ndarray:
    return np.array([model.score(dict(row)) for _, row in X.iterrows()])


@pytest.mark.parametrize("Model", [LogisticMetaFilter, GBMMetaFilter])
def test_learns_planted_signal(Model):
    Xtr, ytr = _planted(500, seed=1)
    Xte, yte = _planted(300, seed=2)
    model = Model().fit(Xtr, ytr)
    auc = roc_auc_score(yte, _scores(model, Xte))
    assert auc > 0.7


@pytest.mark.parametrize("Model", [LogisticMetaFilter, GBMMetaFilter])
def test_stays_near_half_on_noise(Model):
    Xtr, ytr = _noise(500, seed=3)
    Xte, yte = _noise(300, seed=4)
    model = Model().fit(Xtr, ytr)
    auc = roc_auc_score(yte, _scores(model, Xte))
    assert 0.38 < auc < 0.62


@pytest.mark.parametrize("Model", [LogisticMetaFilter, GBMMetaFilter])
def test_calibration_monotone_in_positive_feature(Model):
    Xtr, ytr = _planted(500, seed=5)
    kw = {"monotone_constraints": {"f1": 1}} if Model is GBMMetaFilter else {}
    model = Model(**kw).fit(Xtr, ytr)
    sweep = np.linspace(-3, 3, 25)
    probs = [model.score({"f1": v, "f2": 0.0, "f3": 0.0}) for v in sweep]
    diffs = np.diff(probs)
    assert (diffs >= -1e-9).all()  # non-decreasing in f1
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_logistic_explain_sign_matches_planted():
    Xtr, ytr = _planted(500, seed=6)
    model = LogisticMetaFilter().fit(Xtr, ytr)
    contrib = dict(model.explain({"f1": 2.0, "f2": 2.0, "f3": 0.0}, top_k=3))
    assert contrib["f1"] > 0  # planted positive
    assert contrib["f2"] < 0  # planted negative


def test_gbm_explain_ranks_signal_feature():
    Xtr, ytr = _planted(500, seed=7)
    model = GBMMetaFilter().fit(Xtr, ytr)
    top = model.explain({"f1": 2.5, "f2": 0.0, "f3": 0.0}, top_k=1)
    assert top[0][0] == "f1"


@pytest.mark.parametrize("Model", [LogisticMetaFilter, GBMMetaFilter])
def test_missing_feature_raises(Model):
    Xtr, ytr = _planted(300, seed=8)
    model = Model().fit(Xtr, ytr)
    with pytest.raises(ValueError, match="missing feature"):
        model.score({"f1": 1.0, "f2": 0.0})  # f3 missing
    with pytest.raises(ValueError, match="missing feature"):
        model.explain({"f1": 1.0, "f2": 0.0})


@pytest.mark.parametrize("Model", [LogisticMetaFilter, GBMMetaFilter])
def test_save_load_roundtrip_identical_scores(Model, tmp_path):
    Xtr, ytr = _planted(400, seed=9)
    Xte, _ = _planted(50, seed=10)
    model = Model().fit(Xtr, ytr, trained_range=("2026-01-01", "2026-06-30"))
    before = _scores(model, Xte)
    path = model.save(tmp_path / "m.joblib")
    assert path.with_suffix(path.suffix + ".meta.json").exists()
    loaded = Model.load(path)
    after = _scores(loaded, Xte)
    np.testing.assert_allclose(before, after, rtol=0, atol=0)
    assert loaded.model_id == model.model_id
    assert loaded.feature_names == model.feature_names


@pytest.mark.parametrize("Model", [LogisticMetaFilter, GBMMetaFilter])
def test_lightweight_budget(Model):
    Xtr, ytr = _planted(400, seed=11)
    model = Model().fit(Xtr, ytr)
    report = model.lightweight_check(n_calls=1000)
    assert report["ok"]
    assert report["median_inference_ms"] < 1.0
    assert report["artifact_bytes"] < 5 * 1024 * 1024


def test_gbm_rejects_oversized_hyperparams():
    with pytest.raises(ValueError):
        GBMMetaFilter(max_depth=6)
    with pytest.raises(ValueError):
        GBMMetaFilter(n_estimators=500)
    with pytest.raises(ValueError):
        GBMMetaFilter(num_leaves=64)


def test_fit_requires_two_classes():
    X = pd.DataFrame({c: np.arange(10, dtype=float) for c in FEATURES})
    with pytest.raises(ValueError, match="two classes"):
        LogisticMetaFilter().fit(X, np.ones(10, dtype=int))
