"""Exit-study simulator: next-open fills, stop-first, signal exits, costs."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.decision.exit_study import indicators, simulate

ZERO = lambda i, px: 0.0  # noqa: E731


def _day(closes, lows=None):
    c = np.array(closes, float)
    l = np.array(lows, float) if lows is not None else c - 0.05
    return pd.DataFrame({"open": c, "high": c + 0.05, "low": l, "close": c,
                         "volume": np.full(len(c), 1000.0), "minute_of_day": 570 + np.arange(len(c))})


def test_ema9_exit_lets_runner_run_while_bracket_caps_it():
    c = [10.0] * 30 + [10.2, 10.6, 11.2, 12.0, 13.0, 14.0, 15.0, 16.0, 15.0, 14.0, 13.0, 12.0] + [12.0] * 10
    day = _day(c)
    ind = indicators(day)
    t = 30
    br = simulate(day, ind, t, "bracket", len(c) - 1, ZERO)
    e9 = simulate(day, ind, t, "ema9", len(c) - 1, ZERO)
    assert br.net < 0.10 < e9.net             # bracket takes ~2 ATR; ema9 rides the run


def test_stop_checked_first_and_gap_through_fills_at_open():
    c = [10.0] * 30 + [10.0, 10.0, 9.0, 9.0]
    lows = [9.95] * 30 + [9.95, 9.97, 8.5, 8.5]
    day = _day(c, lows)
    day.loc[32, "open"] = 9.0                 # gaps below the 9.95 structural stop
    tr = simulate(day, indicators(day), 30, "macd_fade", len(c) - 1, ZERO)
    assert tr.exit_i == 32 and tr.exit == 9.0
