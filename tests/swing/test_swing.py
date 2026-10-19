"""Swing labels (next-open entry, stop-first, gap fills, truncation) and point-in-time features."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.swing.features import symbol_features
from sgt.swing.labels import SwingLabel, bracket_labels

CFG = SwingLabel(k_tp=2.0, k_sl=1.0, h=3)


def _arr(rows):
    o, h, l, c = (np.array(x, float) for x in zip(*rows))
    return o, h, l, c


def test_target_hit_and_costs():
    o, h, l, c = _arr([(10, 10, 10, 10), (10, 10.5, 9.8, 10.2), (10.3, 12.5, 10.2, 12)])
    atr = np.full(3, 1.0)
    net, held = bracket_labels(o, h, l, c, atr, [0], SwingLabel(2.0, 1.0, 2), np.array([0.0]))
    assert held[0] == 2 and abs(net[0] - (12.0 / 10.0 - 1)) < 1e-12
    net2, _ = bracket_labels(o, h, l, c, atr, [0], SwingLabel(2.0, 1.0, 2), np.array([0.01]))
    assert abs(net2[0] - (12.0 * 0.99 / (10.0 * 1.01) - 1)) < 1e-12


def test_stop_first_when_both_touched_and_gap_through_stop():
    both = _arr([(10, 10, 10, 10), (10, 12.5, 8.9, 11), (11, 11, 11, 11), (11, 11, 11, 11)])
    net, held = bracket_labels(*both, np.full(4, 1.0), [0], CFG, np.array([0.0]))
    assert held[0] == 1 and abs(net[0] - (9.0 / 10 - 1)) < 1e-12
    gap = _arr([(10, 10, 10, 10), (10, 10.2, 9.5, 10), (8.0, 8.5, 7.5, 8), (8, 8, 8, 8)])
    net, held = bracket_labels(*gap, np.full(4, 1.0), [0], CFG, np.array([0.0]))
    assert held[0] == 2 and abs(net[0] - (8.0 / 10 - 1)) < 1e-12


def test_timeout_exits_at_close_and_truncation_is_nan():
    rows = [(10, 10, 10, 10)] + [(10, 10.5, 9.5, 10.3)] * 3
    net, held = bracket_labels(*_arr(rows), np.full(4, 1.0), [0], CFG, np.array([0.0]))
    assert held[0] == 3 and abs(net[0] - 0.03) < 1e-12
    net, held = bracket_labels(*_arr(rows), np.full(4, 1.0), [1], CFG, np.array([0.0]))
    assert np.isnan(net[0]) and np.isnan(held[0])


def test_features_are_point_in_time():
    rng = np.random.default_rng(0)
    n = 400
    c = 20 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    df = pd.DataFrame({"date": pd.date_range("2020-01-01", periods=n, freq="B").astype(str),
                       "open": c * 0.99, "high": c * 1.02, "low": c * 0.97, "close": c,
                       "volume": rng.uniform(1e5, 1e6, n)})
    base = symbol_features(df)
    future = df.copy()
    future.loc[300:, ["open", "high", "low", "close"]] *= 3.0     # rewrite the future
    future.loc[300:, "volume"] *= 10
    alt = symbol_features(future)
    cols = [c for c in base.columns if c != "trigger"]
    pd.testing.assert_frame_equal(base.loc[:299, cols], alt.loc[:299, cols])
    assert (base.loc[:299, "trigger"].fillna("-") == alt.loc[:299, "trigger"].fillna("-")).all()
