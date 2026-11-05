from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.fast import meta


def test_size_weight_terciles_average_one_on_in_sample():
    ins = np.arange(300.0)
    w = meta.size_weight(ins, ins)
    assert set(w) == {0.5, 1.0, 1.5} and abs(w.mean() - 1.0) < 0.01
    assert list(meta.size_weight(np.array([-1.0, 150.0, 1e9]), ins)) == [0.5, 1.0, 1.5]


def test_news_features_keep_unknown_filings_unknown():
    ev = pd.DataFrame({"news_own": ["True", "False"], "news_earn": [False, True], "known": [1, 0],
                       "f_8k": [1.0, np.nan], "f_dil": [0.0, np.nan]})
    f = meta.news_features(ev)
    assert f.n_own.tolist() == [1.0, 0.0] and f.n_earn.tolist() == [0.0, 1.0]
    assert f.f_8k.iloc[0] == 1.0 and np.isnan(f.f_8k.iloc[1]) and np.isnan(f.f_dil.iloc[1])


def test_brain_features_extend_v1_without_dropping_any():
    assert meta.BRAIN_FEATURES[:len(meta.FEATURES)] == meta.FEATURES and len(set(meta.BRAIN_FEATURES)) == 29
