"""Rule mechanics for the leveraged-ETF close test (scripts/letf_close_study.py)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import letf_close_study as lc  # noqa: E402


def table(n: int, start: str = "2024-01-02") -> pd.DataFrame:
    days = pd.bdate_range(start, periods=n).date
    rng = np.random.default_rng(1)
    c = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
    return pd.DataFrame({"first": c, "close": c, "last_min": lc.LAST, "p1530": c, "c1559": c}, index=days)


def test_signal_direction_threshold_and_costs():
    s = table(40)
    d = s.index[30]
    s.loc[d, "p1530"] = s.c1559.iloc[29] * 1.10           # +10% by 15:30: far above sd20
    s.loc[d, "c1559"] = s.loc[d, "p1530"] * 1.01
    t = lc.trades(s, date(2020, 1, 1))
    row = t.loc[d]
    assert row.side == 1 and row.gross == pytest.approx(0.01)
    assert row.net == pytest.approx(0.01 - lc.side_cost(row.p1530) - lc.side_cost(row.c1559))
    assert (t.r.abs() >= t.sd20).all()


def test_periods_pre_gap_post():
    s = table(80)
    s["p1530"] = s.c1559.shift(1) * np.where(np.arange(80) % 2, 1.05, 0.95)   # every day trades
    launch = s.index[40]
    t = lc.trades(s, launch)
    assert set(t[t.index < launch].period) == {"pre"}
    gap = [d for d in s.index if launch <= d < s.index[40 + lc.POST_LAG]]
    assert not set(gap) & set(t.index)                                        # 20-session gap skipped
    assert set(t[t.index >= s.index[60]].period) == {"post"}


def test_early_close_and_split_skipped():
    s = table(60)
    s["p1530"] = s.c1559.shift(1) * 1.05
    s.loc[s.index[45], "last_min"] = 779                                      # 12:59 close
    s.loc[s.index[50]:, ["first", "close", "p1530", "c1559"]] /= 10           # reverse split-like jump
    t = lc.trades(s, date(2020, 1, 1))
    assert s.index[45] not in t.index
    assert not any(d in t.index for d in s.index[50:])                       # 20 sessions after the jump
