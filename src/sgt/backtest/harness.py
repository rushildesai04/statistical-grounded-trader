"""Walk-forward backtest harness. NO shuffled split — ever. Train on a rolling
window of past bars, test on the next fold, roll forward. Every candidate model
is reported side-by-side with the majority and logistic baselines, net of costs.
Promotion requires beating both.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from sgt.backtest.metrics import accuracy, base_rate, traded_pnl
from sgt.config import get_settings
from sgt.models.baselines import LogisticBaseline, MajorityClass, Model


@dataclass
class FoldResult:
    fold: int
    n_test: int
    accuracy: float
    base_rate: float
    pnl: dict


@dataclass
class ModelReport:
    model_name: str
    folds: list[FoldResult] = field(default_factory=list)

    def summary(self) -> dict:
        if not self.folds:
            return {"model": self.model_name, "n_test": 0}
        w = np.array([f.n_test for f in self.folds], dtype=float)
        wmean = lambda xs: float(np.average(xs, weights=w)) if w.sum() else 0.0
        return {
            "model": self.model_name,
            "n_test": int(w.sum()),
            "accuracy": wmean([f.accuracy for f in self.folds]),
            "base_rate": wmean([f.base_rate for f in self.folds]),
            "edge_vs_base": wmean([f.accuracy - f.base_rate for f in self.folds]),
            "net_pnl": float(sum(f.pnl["net_pnl"] for f in self.folds)),
            "n_trades": int(sum(f.pnl["n_trades"] for f in self.folds)),
            "sharpe": wmean([f.pnl["sharpe_annualized"] for f in self.folds]),
            "max_drawdown": float(max((f.pnl["max_drawdown"] for f in self.folds), default=0.0)),
        }


def walk_forward(df: pd.DataFrame, model_factory, n_splits: int = 5,
                 cost: float | None = None) -> ModelReport:
    """Expanding-window walk-forward over time-ordered folds."""
    cost = cost if cost is not None else get_settings().cost_bps / 10_000.0
    report = ModelReport(model_name=model_factory().name)
    if df.empty:
        return report

    df = df.sort_values("as_of_ts").reset_index(drop=True)
    dates = np.array_split(df["as_of_ts"].unique(), n_splits + 1)
    for k in range(1, len(dates)):
        train_dates = np.concatenate(dates[:k])
        test_dates = dates[k]
        train = df[df["as_of_ts"].isin(train_dates)]
        test = df[df["as_of_ts"].isin(test_dates)]
        if train.empty or test.empty:
            continue
        model: Model = model_factory().fit(train)
        pred = model.predict(test)
        report.folds.append(FoldResult(
            fold=k, n_test=len(test),
            accuracy=accuracy(test["cls"], pred),
            base_rate=base_rate(test["cls"]),
            pnl=traded_pnl(pred, test["fwd_return"], cost),
        ))
    return report


def run_all(df: pd.DataFrame, candidate_factory=None, n_splits: int = 5,
            cost: float | None = None) -> dict:
    """Run baselines (+ optional candidate) and assemble a comparison report
    with the promotion gate applied."""
    factories = {"majority": MajorityClass, "logistic": LogisticBaseline}
    if candidate_factory is not None:
        factories[candidate_factory().name] = candidate_factory

    summaries = {name: walk_forward(df, f, n_splits, cost).summary()
                 for name, f in factories.items()}

    gate = None
    if candidate_factory is not None:
        cand = candidate_factory().name
        gate = _promotion_gate(summaries, cand)
    return {"models": summaries, "promotion": gate}


def _promotion_gate(summaries: dict, candidate: str) -> dict:
    c = summaries[candidate]
    maj, log = summaries["majority"], summaries["logistic"]
    beats_maj = c["net_pnl"] > maj["net_pnl"] and c["edge_vs_base"] > 0
    beats_log = c["net_pnl"] > log["net_pnl"]
    return {
        "candidate": candidate,
        "beats_majority": bool(beats_maj),
        "beats_logistic": bool(beats_log),
        "promote": bool(beats_maj and beats_log),
    }
