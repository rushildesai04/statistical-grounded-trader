"""Monthly expanding-window walk-forward: each month's model sees only earlier months.

The concatenated monthly predictions are exactly what a model retrained every month would have
traded live. Hyperparameters and thresholds are frozen by the pre-registration.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.megacap.features import FEATURES

LGB_PARAMS = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=200,
                  colsample_bytree=0.8, subsample=0.8, subsample_freq=1, random_state=0, verbose=-1)
Q_LOW, Q_HIGH = 0.10, 0.90
MIN_TRAIN_SESSIONS = 250


def fit_predict(train: pd.DataFrame, test: pd.DataFrame, features: list[str],
                params: dict | None = None) -> tuple[np.ndarray, float, float]:
    import lightgbm as lgb

    y = train.label.to_numpy(float)
    lo, hi = np.quantile(y, [0.01, 0.99])
    model = lgb.LGBMRegressor(**(params or LGB_PARAMS))
    model.fit(train[features], np.clip(y, lo, hi))
    ins = model.predict(train[features])
    q_lo, q_hi = np.quantile(ins, [Q_LOW, Q_HIGH])
    return model.predict(test[features]), float(q_lo), float(q_hi)


def walk_forward(df: pd.DataFrame, start: str, *, features: list[str] | None = None,
                 params: dict | None = None) -> pd.DataFrame:
    """``df``: rows with ``session`` (date), FEATURES, ``label``. Returns test rows with
    ``pred`` and ``position`` (+1 / -1 / 0) for every month from ``start``."""
    feats = list(features or FEATURES)
    d = df.copy()
    d["month"] = pd.to_datetime(d.session).dt.to_period("M")
    out = []
    for m in sorted(d.month.unique()):
        if m < pd.Period(start, "M"):
            continue
        train, test = d[d.month < m], d[d.month == m]
        if train.session.nunique() < MIN_TRAIN_SESSIONS or test.empty:
            continue
        pred, q_lo, q_hi = fit_predict(train, test, feats, params) if params else fit_predict(train, test, feats)
        t = test.assign(pred=pred, q_lo=q_lo, q_hi=q_hi)
        t["position"] = np.where(pred >= q_hi, 1, np.where(pred <= q_lo, -1, 0))
        out.append(t)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def net_returns(t: pd.DataFrame, cost_rt: float) -> pd.Series:
    """Net return per row: position x label minus the round-trip cost on every trade."""
    return t.position * t.label - (t.position != 0) * cost_rt


def daily_portfolio(trades: dict[str, pd.DataFrame], costs: dict[str, float], mult: float = 1.0) -> pd.Series:
    """Mean over tickers (that have rows that day) of each ticker's summed daily net return."""
    per = []
    for sym, t in trades.items():
        if t.empty:
            continue
        net = net_returns(t, costs[sym] * mult)
        per.append(net.groupby(t.session).sum().rename(sym))
    if not per:
        return pd.Series(dtype=float)
    return pd.concat(per, axis=1).mean(axis=1, skipna=True).sort_index()
