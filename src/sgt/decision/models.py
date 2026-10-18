"""Decision models: mu(x), sigma(x), p(x), b(x) (DECISION_MODEL.md §6, §10 F4/F7).

- ``mu`` expected net return: ensemble mean of ridge-on-standardized-features and a
  shallow LightGBM regressor, each bagged over bootstrap resamples of SESSIONS (not
  rows) so correlated same-day signals stay together.
- ``sigma`` uncertainty: dispersion (std) of the bagged members' predictions.
- ``p`` win probability: a calibrated classifier (reuses ``sgt.learning.meta_filter``).
- ``b`` payoff ratio: avg win / avg |loss| tabulated by predicted-probability decile,
  shrunk toward the global ratio by estimation-error weight.

Model complexity is tied to effective N = distinct (symbol, session) clusters (F4):
<300 -> <=5 features, ridge only; <1000 -> <=12 features, ridge + GBM; else full set,
ridge + GBM. For the capped tiers the feature subset is chosen by stability selection
(kept iff selected in >= 80% of bootstrap fits) INSIDE training only.

Missing values stay NaN: the GBM handles them natively; the ridge model gets train-set
medians plus a missing-indicator column. Deterministic throughout (fixed seeds).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from sgt.learning.meta_filter import GBMMetaFilter, LogisticMetaFilter

ARTIFACT_VERSION = "1"

# F1 (DECISION_MODEL.md §10): only a run whose training verdict cleared the promotion
# gates AND whose dataset was promotion-eligible may back a trading artifact. Any other
# verdict (NO-TRADE, INVALID, research-only) is quarantined and refused at the load path.
PROMOTABLE_VERDICTS = frozenset({"PROMOTE-CANDIDATE", "PROMOTE-CANDIDATE-UNVERIFIED-COSTS"})

_SEED = 17
_N_BAG = 5
_N_STABILITY = 20
_STABILITY_KEEP = 0.80
_RIDGE_ALPHA = 1.0

TIER_SMALL = "ridge5"
TIER_MEDIUM = "ridge_gbm12"
TIER_FULL = "full"


@dataclass(frozen=True, slots=True)
class Tier:
    name: str
    max_features: int | None   # None == all
    use_gbm: bool


def artifact_is_eligible(metadata: Mapping) -> bool:
    """True iff an artifact's metadata marks it fit for trading (F1).

    Requires a promotable training ``verdict`` AND an explicit ``promotion_eligible``
    flag from the dataset. Missing / unknown fields fail closed (ineligible)."""
    return (str(metadata.get("verdict", "")) in PROMOTABLE_VERDICTS
            and bool(metadata.get("promotion_eligible", False)))


def select_tier(n_eff: int) -> Tier:
    """Pick the complexity tier from the effective cluster count (F4)."""
    if n_eff < 300:
        return Tier(TIER_SMALL, 5, False)
    if n_eff < 1000:
        return Tier(TIER_MEDIUM, 12, True)
    return Tier(TIER_FULL, None, True)


@dataclass(frozen=True, slots=True)
class ModelPrediction:
    mu: float
    sigma: float
    p: float
    b: float
    contributions: list[tuple[str, float]] = field(default_factory=list)


def effective_n(df: pd.DataFrame, symbol_col: str = "symbol",
                session_col: str = "session_date") -> int:
    """Number of distinct (symbol, session) clusters carrying a signal (F4)."""
    if symbol_col not in df.columns or session_col not in df.columns:
        raise KeyError(f"dataset must have {symbol_col!r} and {session_col!r} columns")
    return int(df.loc[:, [symbol_col, session_col]].drop_duplicates().shape[0])


def _bootstrap_sessions(sessions: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Row mask selecting all rows whose session is in a with-replacement session resample."""
    uniq = np.unique(sessions)
    drawn = rng.choice(uniq, size=uniq.size, replace=True)
    counts: dict = {}
    for s in drawn:
        counts[s] = counts.get(s, 0) + 1
    # Row multiplicity = how many times its session was drawn (session-level bootstrap).
    return np.array([counts.get(s, 0) for s in sessions], dtype=float)


class _Prep:
    """Median imputation + missing indicators for the linear model / classifier."""

    def __init__(self, features: Sequence[str]) -> None:
        self.features = tuple(features)
        self.medians: np.ndarray | None = None
        self.indicator_features: tuple[str, ...] = ()

    def fit(self, X: np.ndarray) -> "_Prep":
        self.medians = np.nanmedian(X, axis=0)
        self.medians = np.where(np.isfinite(self.medians), self.medians, 0.0)
        has_nan = np.isnan(X).any(axis=0)
        self.indicator_features = tuple(
            f for f, flag in zip(self.features, has_nan) if flag)
        return self

    @property
    def columns(self) -> list[str]:
        return list(self.features) + [f"{f}__isna" for f in self.indicator_features]

    def transform(self, X: np.ndarray) -> np.ndarray:
        assert self.medians is not None
        nan_mask = np.isnan(X)
        imputed = np.where(nan_mask, self.medians, X)
        if not self.indicator_features:
            return imputed
        idx = [self.features.index(f) for f in self.indicator_features]
        indicators = nan_mask[:, idx].astype(float)
        return np.concatenate([imputed, indicators], axis=1)


def _stability_select(X: np.ndarray, y: np.ndarray, sessions: np.ndarray,
                      features: Sequence[str], cap: int, seed: int) -> list[str]:
    """Return <= cap features kept in >= 80% of bootstrap ridge fits (F4)."""
    rng = np.random.default_rng(seed)
    n_feat = len(features)
    counts = np.zeros(n_feat, dtype=float)
    medians = np.nanmedian(X, axis=0)
    medians = np.where(np.isfinite(medians), medians, 0.0)
    X_imp = np.where(np.isnan(X), medians, X)
    for _ in range(_N_STABILITY):
        w = _bootstrap_sessions(sessions, rng)
        mask = w > 0
        if mask.sum() < max(10, n_feat + 1) or len(np.unique(y[mask])) < 2:
            continue
        scaler = StandardScaler().fit(X_imp[mask], sample_weight=w[mask])
        ridge = Ridge(alpha=_RIDGE_ALPHA, random_state=seed)
        ridge.fit(scaler.transform(X_imp[mask]), y[mask], sample_weight=w[mask])
        order = np.argsort(-np.abs(ridge.coef_))[:cap]
        counts[order] += 1.0
    freq = counts / _N_STABILITY
    keep = [i for i in range(n_feat) if freq[i] >= _STABILITY_KEEP]
    if not keep:  # fall back to the most-often-selected features
        keep = list(np.argsort(-freq)[:cap])
    keep = sorted(keep, key=lambda i: -freq[i])[:cap]
    return [features[i] for i in sorted(keep)]


class DecisionModel:
    """Fitted mu/sigma/p/b ensemble. Deterministic; inference < 2 ms per candidate."""

    def __init__(self) -> None:
        self.tier: Tier | None = None
        self.n_eff: int = 0
        self.selected_features: tuple[str, ...] = ()
        self.metadata: dict = {}
        self._prep: _Prep | None = None
        self._ridge: list[tuple[StandardScaler, Ridge]] = []
        self._gbm: list = []
        self._p = None
        self._b_edges: np.ndarray | None = None
        self._b_table: np.ndarray | None = None
        self._b_global: float = 1.0
        self._compiled: dict | None = None  # numpy fast-path for inference

    # --------------------------------------------------------------------- fit
    def fit(
        self,
        df: pd.DataFrame,
        feature_cols: Sequence[str],
        label_col: str,
        *,
        symbol_col: str = "symbol",
        session_col: str = "session_date",
        weight_col: str | None = None,
        data_range: tuple[str, str] | None = None,
        config_hash: str = "",
        trial_ids: Sequence[str] = (),
        seed: int = _SEED,
    ) -> "DecisionModel":
        feature_cols = list(feature_cols)
        missing = [c for c in feature_cols + [label_col, symbol_col, session_col]
                   if c not in df.columns]
        if missing:
            raise KeyError(f"dataset missing columns: {missing}")
        y = df[label_col].to_numpy(dtype=float)
        if not np.isfinite(y).all():
            raise ValueError(f"label column {label_col!r} contains non-finite values")
        if len(np.unique(np.sign(y))) < 2 and np.allclose(y, y[0]):
            raise ValueError("label column is constant; cannot fit")

        sessions = df[session_col].astype(str).to_numpy()
        self.n_eff = effective_n(df, symbol_col, session_col)
        self.tier = select_tier(self.n_eff)
        X_all = df[feature_cols].to_numpy(dtype=float)

        if self.tier.max_features is not None and len(feature_cols) > self.tier.max_features:
            self.selected_features = tuple(
                _stability_select(X_all, y, sessions, feature_cols,
                                  self.tier.max_features, seed))
        else:
            self.selected_features = tuple(feature_cols)

        X = df[list(self.selected_features)].to_numpy(dtype=float)
        weights = (df[weight_col].to_numpy(dtype=float) if weight_col
                   else np.ones(len(df), dtype=float))
        if weight_col and not np.isfinite(weights).all():
            raise ValueError(f"weight column {weight_col!r} has non-finite values")

        self._prep = _Prep(self.selected_features).fit(X)
        Xp = self._prep.transform(X)
        self._fit_members(Xp, X, y, sessions, weights, seed)
        self._fit_probability(Xp, y, weights)
        self._fit_payoff(Xp, y)

        self.metadata = {
            "version": ARTIFACT_VERSION,
            "tier": self.tier.name,
            "n_eff": self.n_eff,
            "feature_list": list(self.selected_features),
            "prep_columns": self._prep.columns,
            "label_col": label_col,
            "data_range": list(data_range) if data_range else None,
            "config_hash": config_hash,
            "trial_ids": list(trial_ids),
            "model_id": self._model_id(config_hash),
        }
        return self

    def _fit_members(self, Xp, X_raw, y, sessions, weights, seed) -> None:
        rng = np.random.default_rng(seed)
        self._ridge, self._gbm = [], []
        for _ in range(_N_BAG):
            w = _bootstrap_sessions(sessions, rng) * weights
            mask = w > 0
            if mask.sum() < 5:
                continue
            scaler = StandardScaler().fit(Xp[mask], sample_weight=w[mask])
            ridge = Ridge(alpha=_RIDGE_ALPHA, random_state=seed)
            ridge.fit(scaler.transform(Xp[mask]), y[mask], sample_weight=w[mask])
            self._ridge.append((scaler, ridge))
        if not self._ridge:
            raise ValueError("no ridge member could be fit (too few sessions)")

        if self.tier and self.tier.use_gbm:
            from lightgbm import LGBMRegressor
            rng_g = np.random.default_rng(seed + 1)
            for _ in range(_N_BAG):
                w = _bootstrap_sessions(sessions, rng_g) * weights
                mask = w > 0
                if mask.sum() < 20:
                    continue
                gbm = LGBMRegressor(
                    max_depth=4, n_estimators=300, num_leaves=15, learning_rate=0.05,
                    deterministic=True, random_state=seed, seed=seed, n_jobs=1, verbose=-1)
                gbm.fit(X_raw[mask], y[mask], sample_weight=w[mask])
                self._gbm.append(gbm)

    def _fit_probability(self, Xp, y, weights) -> None:
        y_cls = (y > 0).astype(int)
        if len(np.unique(y_cls)) < 2:
            raise ValueError("win/loss label has a single class; cannot fit p(x)")
        Xdf = pd.DataFrame(Xp, columns=self._prep.columns)
        self._p = (LogisticMetaFilter() if (self.tier and not self.tier.use_gbm)
                   else GBMMetaFilter())
        self._p.fit(Xdf, y_cls)

    def _fit_payoff(self, Xp, y) -> None:
        p_scores = self._p_scores(Xp)
        self._b_edges = np.quantile(p_scores, np.linspace(0.0, 1.0, 11))
        self._b_edges[0], self._b_edges[-1] = -np.inf, np.inf
        wins, losses = y[y > 0], y[y < 0]
        self._b_global = (float(wins.mean()) / abs(float(losses.mean()))
                          if wins.size and losses.size else 1.0)
        bins = np.clip(np.digitize(p_scores, self._b_edges[1:-1]), 0, 9)
        table = np.full(10, self._b_global, dtype=float)
        for d in range(10):
            dy = y[bins == d]
            dw, dl = dy[dy > 0], dy[dy < 0]
            if dw.size and dl.size:
                raw = float(dw.mean()) / abs(float(dl.mean()))
                k = 20.0  # shrink toward the global ratio by estimation-error weight
                shrink = dy.size / (dy.size + k)
                table[d] = shrink * raw + (1.0 - shrink) * self._b_global
        self._b_table = table

    # ------------------------------------------------------------- inference
    def _compile(self) -> dict:
        """Precompute dense ridge arrays so single-row inference avoids sklearn's
        per-call validation overhead (keeps predict() < 2 ms)."""
        if self._compiled is None:
            self._compiled = {
                "mean": np.array([s.mean_ for s, _ in self._ridge]),
                "scale": np.array([s.scale_ for s, _ in self._ridge]),
                "coef": np.array([r.coef_ for _, r in self._ridge]),
                "intercept": np.array([float(r.intercept_) for _, r in self._ridge]),
            }
        return self._compiled

    def _ridge_members(self, Xp_row: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Return (member predictions, standardized rows) for the single input row."""
        c = self._compile()
        std = (Xp_row - c["mean"]) / c["scale"]          # (M, P)
        preds = (std * c["coef"]).sum(axis=1) + c["intercept"]
        return preds, std

    def _p_scores(self, Xp: np.ndarray) -> np.ndarray:
        return self._p._calibrated.predict_proba(Xp)[:, self._p._pos_index]

    def _vectorize(self, features: Mapping[str, float]) -> np.ndarray:
        missing = [f for f in self.selected_features if f not in features]
        if missing:
            raise ValueError(f"missing feature(s): {missing}")
        return np.array([float(features[f]) for f in self.selected_features],
                        dtype=float).reshape(1, -1)

    def predict(self, features: Mapping[str, float]) -> ModelPrediction:
        if self._prep is None or self._p is None:
            raise RuntimeError("model is not fitted")
        X_row = self._vectorize(features)
        Xp_row = self._prep.transform(X_row)
        ridge_preds, std = self._ridge_members(Xp_row)
        members = ridge_preds
        if self._gbm:
            gbm_preds = np.array([float(g.predict(X_row)[0]) for g in self._gbm])
            members = np.concatenate([ridge_preds, gbm_preds])
        mu, sigma = float(members.mean()), float(members.std())
        p = float(self._p_scores(Xp_row)[0])
        b = float(self._b_table[int(np.clip(np.digitize([p], self._b_edges[1:-1])[0], 0, 9))])
        return ModelPrediction(mu, sigma, p, b, self._contributions(std, X_row))

    def predict_frame(self, df: pd.DataFrame) -> dict[str, np.ndarray]:
        """Batch mu/sigma/p/b for validation. Returns aligned arrays."""
        X = df[list(self.selected_features)].to_numpy(dtype=float)
        Xp = self._prep.transform(X)
        cols = [r.predict(s.transform(Xp)) for s, r in self._ridge]
        cols += [g.predict(X) for g in self._gbm]
        members = np.vstack(cols)
        mu, sigma = members.mean(axis=0), members.std(axis=0)
        p = self._p_scores(Xp)
        bins = np.clip(np.digitize(p, self._b_edges[1:-1]), 0, 9)
        b = self._b_table[bins]
        return {"mu": mu, "sigma": sigma, "p": p, "b": b}

    def _contributions(self, std, X_row, top_k: int = 6) -> list[tuple[str, float]]:
        agg: dict[str, float] = {f: 0.0 for f in self._prep.columns}
        c = self._compile()
        ridge_contrib = (c["coef"] * std).mean(axis=0)  # avg over members, per column
        for name, v in zip(self._prep.columns, ridge_contrib):
            agg[name] += float(v)
        if self._gbm:
            for g in self._gbm:
                contrib = g.booster_.predict(X_row, pred_contrib=True)[0][:-1]
                for name, c in zip(self.selected_features, contrib):
                    agg[name] += float(c) / len(self._gbm)
        ordered = sorted(agg.items(), key=lambda kv: -abs(kv[1]))
        return [(n, v) for n, v in ordered[:top_k]]

    # ------------------------------------------------------------- id / io
    def set_release_metadata(self, *, verdict: str, promotion_eligible: bool,
                             run_id: str) -> None:
        """Stamp the training verdict / dataset eligibility / run id into the artifact
        metadata before save (F1). The load path reads these to refuse quarantined or
        promotion-ineligible artifacts for trading."""
        self.metadata["verdict"] = str(verdict)
        self.metadata["promotion_eligible"] = bool(promotion_eligible)
        self.metadata["run_id"] = str(run_id)

    def is_promotion_eligible(self) -> bool:
        """Whether this fitted model's metadata clears the F1 trading gate."""
        return artifact_is_eligible(self.metadata)

    def _model_id(self, config_hash: str) -> str:
        h = hashlib.sha256()
        h.update(ARTIFACT_VERSION.encode())
        h.update("\x00".join(self.selected_features).encode())
        h.update(json.dumps({"tier": self.tier.name, "n_eff": self.n_eff,
                             "config_hash": config_hash}, sort_keys=True).encode())
        return h.hexdigest()[:16]

    def save(self, path: str | Path) -> Path:
        if self._prep is None:
            raise RuntimeError("cannot save an unfitted model")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({
            "version": ARTIFACT_VERSION, "tier": self.tier, "n_eff": self.n_eff,
            "selected_features": self.selected_features, "metadata": self.metadata,
            "prep": self._prep, "ridge": self._ridge, "gbm": self._gbm, "p": self._p,
            "b_edges": self._b_edges, "b_table": self._b_table, "b_global": self._b_global,
        }, path, compress=3)
        path.with_suffix(path.suffix + ".meta.json").write_text(
            json.dumps(self.metadata, indent=2, sort_keys=True, default=str))
        return path

    @classmethod
    def load(cls, path: str | Path) -> "DecisionModel":
        payload = joblib.load(Path(path))
        obj = cls()
        obj.tier = payload["tier"]
        obj.n_eff = payload["n_eff"]
        obj.selected_features = tuple(payload["selected_features"])
        obj.metadata = payload["metadata"]
        obj._prep = payload["prep"]
        obj._ridge = payload["ridge"]
        obj._gbm = payload["gbm"]
        obj._p = payload["p"]
        obj._b_edges = payload["b_edges"]
        obj._b_table = payload["b_table"]
        obj._b_global = payload["b_global"]
        return obj
