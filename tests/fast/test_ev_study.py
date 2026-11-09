from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_ev_study as ev  # noqa: E402


def test_groups_and_design_are_point_in_time_categories():
    g = ev.groups(pd.Series(["2026-11-10T14:40:00+00:00", "2026-11-10T19:30:00+00:00"]), pd.Series([1.5, 12.0]),
                  pd.Series([0.01, np.nan]))
    assert g.to_dict("records") == [{"window": "pre11", "band": "$1-2", "vol": "calm"},
                                    {"window": "14-16", "band": "$10-50", "vol": "mid"}]   # unknown sigma -> mid
    assert ev.design(g).shape == (2, 1 + 2 + 3 + 2)


def test_ridge_shrinks_toward_the_pooled_mean_and_weights_are_bounded():
    X = np.column_stack([np.ones(4), [0, 0, 1, 1]])
    y = np.array([0.0, 0.0, 0.1, 0.1])
    loose, tight = ev.fit_ridge(X, y, 0.0), ev.fit_ridge(X, y, 1e6)
    assert loose[1] > 0.09 and abs(tight[1]) < 1e-4
    assert ev.weights(np.array([-1.0, 0.01, 0.05]), 0.01).tolist() == [0.25, 1.0, 2.0]


def test_frozen_model_file_matches_the_prereg():
    import json
    m = json.loads(ev.MODEL.read_text())
    assert m["alpha"] == 200.0 and m["lo"] == 0.25 and m["hi"] == 2.0 and m["prereg"].endswith("evsize_2026-11-09.md")
    assert len(m["coef"]) == 1 + 2 + 3 + 2
