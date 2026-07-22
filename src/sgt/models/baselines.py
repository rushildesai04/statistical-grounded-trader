"""Baseline models. Every future model (LSTM, transformer) must beat these
net of costs or it is rejected. Common fit/predict interface over the training
frame produced by features.store.get_training_frame.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
import pandas as pd

from sgt.features.store import FEATURE_COLUMNS

CLASSES = ["down", "flat", "up"]


class Model(ABC):
    name = "model"

    @abstractmethod
    def fit(self, train: pd.DataFrame) -> "Model": ...

    @abstractmethod
    def predict(self, test: pd.DataFrame) -> list[str]:
        """Return predicted class per row, aligned to test.index order."""
        ...


class MajorityClass(Model):
    """Sanity floor: always predict the most common training class."""
    name = "majority"

    def fit(self, train):
        self._cls = train["cls"].mode().iloc[0] if not train.empty else "flat"
        return self

    def predict(self, test):
        return [self._cls] * len(test)


class LogisticBaseline(Model):
    """The real bar to clear: multinomial logistic regression on engineered
    features, standardized. Interpretable and hard to beat on small data."""
    name = "logistic"

    def __init__(self, C: float = 1.0):
        self.C = C

    def fit(self, train):
        from sklearn.linear_model import LogisticRegression
        from sklearn.pipeline import make_pipeline
        from sklearn.preprocessing import StandardScaler

        X = train[FEATURE_COLUMNS].to_numpy(dtype=float)
        y = train["cls"].to_numpy()
        # multinomial is the default (and only) multiclass mode in sklearn >=1.7.
        self._clf = make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000, C=self.C),
        )
        # guard: need >1 class present
        if len(np.unique(y)) < 2:
            self._fallback = y[0] if len(y) else "flat"
            self._clf = None
        else:
            self._fallback = None
            self._clf.fit(X, y)
        return self

    def predict(self, test):
        if self._clf is None:
            return [self._fallback] * len(test)
        X = test[FEATURE_COLUMNS].to_numpy(dtype=float)
        return list(self._clf.predict(X))
