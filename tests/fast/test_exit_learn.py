from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sgt.fast import exit_learn as xl

T0 = pd.Timestamp("2026-11-05T15:00:00Z")


def rth(closes, highs=None):
    n = len(closes)
    return pd.DataFrame({"ts": [T0 + pd.Timedelta(minutes=i) for i in range(n)], "open": closes, "close": closes,
                         "high": highs or closes, "low": closes, "volume": [1000.0] * n, "segment": ["rth"] * n})


def test_checkpoints_label_hold_vs_cover_now_and_stop_ends_them():
    closes = [10.0] * 15 + [9.0] * 20 + [8.0] * 40               # drifts down: holding beats covering early
    champ, rows = xl.checkpoints(rth(closes), T0, 10.0, "volume_spike")
    assert champ["how"] == "cap" and champ["gross"] == pytest.approx(0.2)
    assert [r["k"] for r in rows] == [10, 20, 30, 40, 50]
    r10 = rows[0]
    assert r10["now_px"] == 10.0 and r10["label"] == pytest.approx((10.0 - 8.0) / 10)   # holding earned 20 points more
    assert r10["x_gain"] == pytest.approx(0.0) and rows[1]["x_gain"] == pytest.approx(10 / 9 - 1)
    highs = [10.0] * 25 + [12.5] + [10.0] * 49                    # stop (12.0) hit in minute 26
    champ, rows = xl.checkpoints(rth([10.0] * 75, highs), T0, 10.0, "news")
    assert champ["how"] == "stop" and [r["k"] for r in rows] == [10, 20]


def test_features_use_only_bars_that_ended_by_the_checkpoint():
    closes = [10.0] * 12 + [9.5] * 63
    _, a = xl.checkpoints(rth(closes), T0, 10.0, "news")
    _, b = xl.checkpoints(rth(closes[:20] + [20.0] * 55), T0, 10.0, "news")   # future changes after minute 20
    assert {k: v for k, v in a[0].items() if k.startswith(("x_", "s_"))} == \
        {k: v for k, v in b[0].items() if k.startswith(("x_", "s_"))}
    assert set(xl.FEATURES) <= set(a[0])


def test_policy_covers_at_the_first_negative_prediction_only():
    rows = pd.DataFrame({"k": [10, 20, 30], "now_px": [9.0, 8.5, 9.5]})
    assert xl.policy_gross(10.0, 0.2, rows, np.array([0.01, -0.02, -0.5])) == (pytest.approx(0.15), 20)
    assert xl.policy_gross(10.0, 0.2, rows, np.array([0.01, 0.02, 0.0])) == (0.2, None)
    assert xl.policy_gross(10.0, 0.2, rows, np.array([-0.004, 0.0, 0.0]), margin=xl.MARGIN) == (0.2, None)
