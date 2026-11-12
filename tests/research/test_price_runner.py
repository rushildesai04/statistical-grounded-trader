"""Logic of prereg_price_runner_2026-11-12 on synthetic data (before the single run)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import price_runner_study as pr  # noqa: E402


def test_ret5_uses_the_six_closes_strictly_before_the_session():
    bars = pd.DataFrame({"symbol": "A", "date": [f"2026-03-0{i}" for i in range(2, 10)],
                         "close": [1.0, 1.1, 1.2, 1.3, 1.4, 2.0, 3.0, 9.0]})
    rows = pd.DataFrame({"symbol": ["A", "A", "B"], "session": ["2026-03-08", "2026-03-07", "2026-03-08"]})
    got = pr.ret5(bars, rows)
    assert got.iloc[0] == pytest.approx(2.0 / 1.0 - 1)            # closes 03-02..03-07: the 03-08 bar is not used
    assert np.isnan(got.iloc[1]) and np.isnan(got.iloc[2])       # only 5 prior closes; unknown symbol


def test_stats_skip_strictly_above_q_and_keep_unknowns():
    f = pd.DataFrame({"session": ["a", "b", "c", "d"], "net": [0.05, -0.10, 0.02, 0.03], "ret5": [0.1, 0.5, np.nan, 0.2]})
    st = pr.stats(f, 0.2)
    assert st["n_skipped"] == 1 and st["skipped"] == pytest.approx(-0.10) and st["n_kept"] == 3   # 0.2 is not above
    assert st["kept"] == pytest.approx((0.05 + 0.02 + 0.03) / 3)


def test_pass_needs_power_p_and_a_negative_skipped_total():
    ok = {"n_skipped": 150, "diff": 0.02, "p": 0.01, "skipped_total": -2.0}
    assert pr.passes(ok, 100)
    assert not pr.passes({**ok, "p": 0.06}, 100) and not pr.passes({**ok, "n_skipped": 99}, 100)
    assert not pr.passes({**ok, "skipped_total": 0.5}, 100) and not pr.passes({**ok, "diff": -0.01}, 100)
