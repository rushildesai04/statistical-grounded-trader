"""Lightweight meta-filter models (plan §9.1).

Two implementations of the ``sgt.core.interfaces.MetaFilter`` protocol:

- ``LogisticMetaFilter``  -> standardized logistic regression (the baseline).
- ``GBMMetaFilter``       -> shallow LightGBM (depth <= 4, <= 200 trees).

Both decide take/skip on a candidate's point-in-time features and return a
*calibrated* probability that the candidate hits its profit barrier first
(triple-barrier label +1). Calibration (``CalibratedClassifierCV``) uses isotonic
regression when there is enough data (n >= 200) and sigmoid/Platt otherwise, with
a time-ordered CV (contiguous, non-shuffled ``KFold`` blocks — never randomly
shuffled) so calibration does not mix samples across time at random.

Hard lightweight budget (plan §9.1): CPU inference < 1 ms median per candidate and
artifact < 5 MB. ``lightweight_check`` asserts both.

Explainability: every decision can be attributed to features — logistic via
coefficient x standardized value, GBM via LightGBM TreeSHAP (``pred_contrib``).
A missing feature is always a ``ValueError``; it is never silently treated as 0.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Mapping

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ARTIFACT_VERSION = "1"
_SEED = 7
_MAX_ARTIFACT_BYTES = 5 * 1024 * 1024
_MAX_MEDIAN_INFERENCE_S = 1e-3


def _calibration_cv(n: int) -> KFold:
    # Contiguous, non-shuffled folds: time-ordered (each fold is a time block) and a
    # full partition of the samples, which ``ensemble=False`` calibration requires.
    n_splits = 5 if n >= 200 else 3
    return KFold(n_splits=n_splits, shuffle=False)


def _calibration_method(n: int) -> str:
    return "isotonic" if n >= 200 else "sigmoid"


class _BaseMetaFilter(ABC):
    """Shared feature-ordering, calibration, persistence and budget logic."""

    def __init__(self) -> None:
        self.feature_names: tuple[str, ...] = ()
        self.model_id: str = ""
        self._calibrated: CalibratedClassifierCV | None = None
        self._trained_range: tuple[str, str] | None = None

    # ----------------------------------------------------------------- fitting
    @abstractmethod
    def _make_estimator(self) -> Pipeline:
        """Estimator fit per calibration fold and (on all data) for explanations."""

    @abstractmethod
    def _fit_explainer(self, X: np.ndarray, y: np.ndarray) -> None:
        """Fit the artifact used by ``explain`` on the full training set."""

    @abstractmethod
    def _raw_fingerprint(self) -> bytes:
        """Stable bytes identifying the fitted model (for the content hash)."""

    def fit(self, X: pd.DataFrame, y, *, trained_range: tuple[str, str] | None = None) -> "_BaseMetaFilter":
        if not isinstance(X, pd.DataFrame):
            raise TypeError("X must be a pandas DataFrame with named feature columns")
        if X.shape[1] == 0:
            raise ValueError("X has no feature columns")
        y_arr = np.asarray(y)
        if len(np.unique(y_arr)) < 2:
            raise ValueError("fit requires at least two classes in y")
        self.feature_names = tuple(str(c) for c in X.columns)
        Xv = X.to_numpy(dtype=float)
        if not np.isfinite(Xv).all():
            raise ValueError("X contains non-finite values; impute before fitting")

        n = len(Xv)
        self._calibrated = CalibratedClassifierCV(
            estimator=self._make_estimator(),
            method=_calibration_method(n),
            cv=_calibration_cv(n),
            ensemble=False,
        )
        self._calibrated.fit(Xv, y_arr)
        # Positive class is label 1 (profit barrier hit first).
        classes = list(self._calibrated.classes_)
        if 1 not in classes:
            raise ValueError("y must contain the positive class label 1")
        self._pos_index = classes.index(1)
        self._fit_explainer(Xv, y_arr)
        self._trained_range = tuple(trained_range) if trained_range else None
        self.model_id = self._compute_model_id()
        return self

    def _compute_model_id(self) -> str:
        h = hashlib.sha256()
        h.update(ARTIFACT_VERSION.encode())
        h.update("\x00".join(self.feature_names).encode())
        h.update(json.dumps(self._trained_range, sort_keys=True).encode())
        h.update(self._raw_fingerprint())
        return h.hexdigest()[:16]

    # -------------------------------------------------------------- inference
    def _vectorize(self, features: Mapping[str, float]) -> np.ndarray:
        missing = [f for f in self.feature_names if f not in features]
        if missing:
            raise ValueError(f"missing feature(s): {missing}")
        try:
            return np.array([float(features[f]) for f in self.feature_names], dtype=float).reshape(1, -1)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"non-numeric feature value: {exc}") from exc

    def score(self, features: Mapping[str, float]) -> float:
        if self._calibrated is None:
            raise RuntimeError("model is not fitted")
        x = self._vectorize(features)
        return float(self._calibrated.predict_proba(x)[0, self._pos_index])

    @abstractmethod
    def explain(self, features: Mapping[str, float], top_k: int = 5) -> list[tuple[str, float]]: ...

    # -------------------------------------------------------------- persistence
    def save(self, path: str | Path) -> Path:
        if self._calibrated is None:
            raise RuntimeError("cannot save an unfitted model")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "kind": type(self).__name__,
            "version": ARTIFACT_VERSION,
            "feature_names": self.feature_names,
            "model_id": self.model_id,
            "trained_range": self._trained_range,
            "pos_index": self._pos_index,
            "calibrated": self._calibrated,
            "explainer": self._explainer_state(),
        }
        joblib.dump(payload, path, compress=3)
        meta = {
            "kind": payload["kind"], "version": ARTIFACT_VERSION, "model_id": self.model_id,
            "feature_names": list(self.feature_names), "trained_range": self._trained_range,
        }
        path.with_suffix(path.suffix + ".meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
        return path

    @classmethod
    def load(cls, path: str | Path) -> "_BaseMetaFilter":
        payload = joblib.load(Path(path))
        kind = payload["kind"]
        impl = {c.__name__: c for c in (LogisticMetaFilter, GBMMetaFilter)}.get(kind)
        if impl is None:
            raise ValueError(f"unknown meta-filter kind {kind!r}")
        obj = impl.__new__(impl)
        _BaseMetaFilter.__init__(obj)
        obj.feature_names = tuple(payload["feature_names"])
        obj.model_id = payload["model_id"]
        obj._trained_range = tuple(payload["trained_range"]) if payload["trained_range"] else None
        obj._calibrated = payload["calibrated"]
        obj._pos_index = payload["pos_index"]
        obj._load_explainer_state(payload["explainer"])
        return obj

    @abstractmethod
    def _explainer_state(self) -> dict: ...

    @abstractmethod
    def _load_explainer_state(self, state: dict) -> None: ...

    # -------------------------------------------------------------- budget check
    def lightweight_check(self, n_calls: int = 1000) -> dict:
        """Assert the plan §9.1 hard budget: median inference < 1 ms, artifact < 5 MB."""
        if self._calibrated is None:
            raise RuntimeError("model is not fitted")
        sample = {f: 0.0 for f in self.feature_names}
        self.score(sample)  # warm up
        timings = np.empty(n_calls, dtype=float)
        for i in range(n_calls):
            t0 = time.perf_counter()
            self.score(sample)
            timings[i] = time.perf_counter() - t0
        median_s = float(np.median(timings))

        with tempfile.TemporaryDirectory() as td:
            artifact = self.save(Path(td) / "model.joblib")
            artifact_bytes = int(artifact.stat().st_size)

        report = {
            "median_inference_ms": median_s * 1e3,
            "artifact_bytes": artifact_bytes,
            "ok": median_s < _MAX_MEDIAN_INFERENCE_S and artifact_bytes < _MAX_ARTIFACT_BYTES,
        }
        if median_s >= _MAX_MEDIAN_INFERENCE_S:
            raise AssertionError(f"inference budget exceeded: median {median_s*1e3:.3f} ms >= 1 ms")
        if artifact_bytes >= _MAX_ARTIFACT_BYTES:
            raise AssertionError(f"artifact budget exceeded: {artifact_bytes} bytes >= {_MAX_ARTIFACT_BYTES}")
        return report


class LogisticMetaFilter(_BaseMetaFilter):
    """Standardized logistic regression with balanced class weights."""

    def __init__(self, C: float = 1.0) -> None:
        super().__init__()
        self.C = C
        self._scaler: StandardScaler | None = None
        self._coef: np.ndarray | None = None

    def _make_estimator(self) -> Pipeline:
        return Pipeline([
            ("scaler", StandardScaler()),
            ("lr", LogisticRegression(
                C=self.C, class_weight="balanced", max_iter=1000,
                random_state=_SEED, solver="lbfgs",
            )),
        ])

    def _fit_explainer(self, X: np.ndarray, y: np.ndarray) -> None:
        pipe = self._make_estimator().fit(X, y)
        self._scaler = pipe.named_steps["scaler"]
        lr = pipe.named_steps["lr"]
        # Orient coefficients toward the positive class (label 1).
        pos = list(lr.classes_).index(1)
        coef = lr.coef_[0] if lr.coef_.shape[0] == 1 else lr.coef_[pos]
        self._coef = np.asarray(coef, dtype=float)

    def _raw_fingerprint(self) -> bytes:
        assert self._scaler is not None and self._coef is not None
        return np.concatenate([self._coef, self._scaler.mean_, self._scaler.scale_]).tobytes()

    def explain(self, features: Mapping[str, float], top_k: int = 5) -> list[tuple[str, float]]:
        if self._coef is None or self._scaler is None:
            raise RuntimeError("model is not fitted")
        x = self._vectorize(features).ravel()
        std_x = (x - self._scaler.mean_) / self._scaler.scale_
        contrib = self._coef * std_x
        order = np.argsort(-np.abs(contrib))[:top_k]
        return [(self.feature_names[i], float(contrib[i])) for i in order]

    def _explainer_state(self) -> dict:
        return {"coef": self._coef, "mean": self._scaler.mean_, "scale": self._scaler.scale_}

    def _load_explainer_state(self, state: dict) -> None:
        self._coef = np.asarray(state["coef"], dtype=float)
        self._scaler = StandardScaler()
        self._scaler.mean_ = np.asarray(state["mean"], dtype=float)
        self._scaler.scale_ = np.asarray(state["scale"], dtype=float)
        self._scaler.var_ = self._scaler.scale_ ** 2
        self._scaler.n_features_in_ = len(self._coef)


class GBMMetaFilter(_BaseMetaFilter):
    """Shallow, deterministic LightGBM with optional monotone constraints."""

    def __init__(
        self,
        max_depth: int = 4,
        n_estimators: int = 200,
        num_leaves: int = 15,
        learning_rate: float = 0.05,
        monotone_constraints: Mapping[str, int] | None = None,
    ) -> None:
        super().__init__()
        if max_depth > 4:
            raise ValueError("max_depth must be <= 4 (lightweight budget)")
        if n_estimators > 200:
            raise ValueError("n_estimators must be <= 200 (lightweight budget)")
        if num_leaves > 15:
            raise ValueError("num_leaves must be <= 15 (lightweight budget)")
        self.max_depth = max_depth
        self.n_estimators = n_estimators
        self.num_leaves = num_leaves
        self.learning_rate = learning_rate
        self.monotone_constraints = dict(monotone_constraints) if monotone_constraints else None
        self._explainer = None  # LGBMClassifier fit on all data

    def _monotone_list(self) -> list[int] | None:
        if not self.monotone_constraints:
            return None
        unknown = set(self.monotone_constraints) - set(self.feature_names)
        if unknown:
            raise ValueError(f"monotone_constraints names not in features: {sorted(unknown)}")
        return [int(self.monotone_constraints.get(f, 0)) for f in self.feature_names]

    def _make_estimator(self):
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            max_depth=self.max_depth, n_estimators=self.n_estimators, num_leaves=self.num_leaves,
            learning_rate=self.learning_rate, deterministic=True, random_state=_SEED, seed=_SEED,
            n_jobs=1, verbose=-1, monotone_constraints=self._monotone_list(),
        )

    def _fit_explainer(self, X: np.ndarray, y: np.ndarray) -> None:
        self._explainer = self._make_estimator().fit(X, y)

    def _raw_fingerprint(self) -> bytes:
        assert self._explainer is not None
        return self._explainer.booster_.model_to_string().encode()

    def explain(self, features: Mapping[str, float], top_k: int = 5) -> list[tuple[str, float]]:
        if self._explainer is None:
            raise RuntimeError("model is not fitted")
        x = self._vectorize(features)
        # TreeSHAP: shape (1, n_features + 1); last column is the expected value.
        contrib = self._explainer.booster_.predict(x, pred_contrib=True)[0][:-1]
        order = np.argsort(-np.abs(contrib))[:top_k]
        return [(self.feature_names[i], float(contrib[i])) for i in order]

    def _explainer_state(self) -> dict:
        return {"model_str": self._explainer.booster_.model_to_string(),
                "monotone": self.monotone_constraints}

    def _load_explainer_state(self, state: dict) -> None:
        import lightgbm as lgb

        self.monotone_constraints = state.get("monotone")
        booster = lgb.Booster(model_str=state["model_str"])

        class _BoosterShim:
            def __init__(self, b):
                self.booster_ = b

        self._explainer = _BoosterShim(booster)
