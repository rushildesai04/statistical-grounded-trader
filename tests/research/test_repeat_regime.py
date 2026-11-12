"""Logic of prereg_repeat_regime_2026-11-12 on synthetic data (before the single run)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import repeat_regime_study as rr  # noqa: E402


def test_flag_repeats_looks_1_to_7_days_back_per_symbol():
    ev = pd.DataFrame({"symbol": ["A", "B"], "session": ["2026-03-02", "2026-03-05"]})
    rows = pd.DataFrame({"symbol": ["A", "A", "A", "B", "C"],
                         "session": ["2026-03-02", "2026-03-09", "2026-03-10", "2026-03-06", "2026-03-06"]})
    assert list(rr.flag_repeats(rows, ev)) == [False, True, False, True, False]


def test_prior_counts_use_the_previous_session_and_zero_when_empty():
    ev = pd.DataFrame({"symbol": list("ABC"), "session": ["2026-03-02", "2026-03-02", "2026-03-03"]})
    prev = {"2026-03-03": "2026-03-02", "2026-03-04": "2026-03-03", "2026-03-06": "2026-03-05"}.get
    got = rr.prior_counts(ev, pd.Series(["2026-03-03", "2026-03-04", "2026-03-06"]), prev=prev)
    assert list(got) == [2, 1, 0]
    real = rr.prior_counts(ev, pd.Series(["2026-03-03"]))                 # NYSE calendar: Mon 03-02 before Tue
    assert list(real) == [2]


def _longs(n=500, seed=4):
    rng = np.random.default_rng(seed)
    b = pd.DataFrame({"session": [f"2025-0{1 + i % 9}-{1 + i % 27:02d}" for i in range(n)], "d": rng.uniform(0.01, 0.06, n),
                      "e_net": rng.normal(-0.02, 0.03, n), "repeat": np.arange(n) % 4 == 0})
    return b


def test_r1_compares_per_dollar_a2_of_repeat_and_first_time_longs():
    b = _longs()
    b.loc[b.repeat, "e_net"] += 0.03
    st = rr.r1_stats(b, 0.03)
    w = np.minimum(1.0, 0.03 / b.d)
    exp = (w * b.e_net)[b.repeat].sum() / w[b.repeat].sum() - (w * b.e_net)[~b.repeat].sum() / w[~b.repeat].sum()
    assert st["diff"] == pytest.approx(exp) and st["diff"] > 0 and rr.r1_pass(st)
    assert not rr.r1_pass({**st, "p": 0.03}) and not rr.r1_pass({**st, "n_repeat": 99})


def test_r2_needs_hot_days_to_lose_and_a_positive_p():
    rng = np.random.default_rng(6)
    n = 500
    f = pd.DataFrame({"session": [f"2025-0{1 + i % 9}-{1 + i % 27:02d}" for i in range(n)], "net": rng.normal(0.02, 0.04, n),
                      "hot": np.arange(n) % 3 == 0})
    f.loc[f.hot, "net"] -= 0.05
    st = rr.r2_stats(f)
    assert st["diff"] > 0 and st["hot_total"] < 0 and rr.r2_pass(st)
    assert not rr.r2_pass({**st, "hot_total": 1.0}) and not rr.r2_pass({**st, "n_hot": 99})
    assert not rr.r2_pass({**st, "diff": -0.01, "p": 0.0})


def test_hot_threshold_is_the_training_75th_percentile_of_daily_counts():
    ev = pd.DataFrame({"symbol": list("ABCDEFGHIJ"),
                       "session": ["2026-01-02"] * 1 + ["2026-01-05"] * 2 + ["2026-01-06"] * 3 + ["2026-01-07"] * 4})
    held = pd.DataFrame({"symbol": ["X"] * 9, "session": ["2026-08-03"] * 9})            # held-out never sets it
    assert rr.hot_threshold(pd.concat([ev, held])) == pytest.approx(pd.Series([1, 2, 3, 4]).quantile(0.75))
