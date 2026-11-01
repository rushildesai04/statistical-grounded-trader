"""Trader rules: entry needs a deep prior gap + volume spike in the window; first-fade exit."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.decision.trader_rules import TraderRules, entries, indicators, trade


def _day(closes, vols, start_minute=900):
    c = np.array(closes, float)
    return pd.DataFrame({"open": np.r_[c[0], c[:-1]], "high": c + 0.02, "low": c - 0.02,
                         "close": c, "volume": np.array(vols, float),
                         "minute_of_day": start_minute + np.arange(len(c))})


def _setup(spike=10.0):
    down = list(np.linspace(10.0, 9.0, 30))           # EMA9 well below EMA24
    up = [9.05, 9.12, 9.2, 9.3, 9.42, 9.55, 9.7, 9.8, 9.82, 9.78]
    vols = [1000.0] * 34 + [1000.0 * spike] * 6 + [1000.0] * 10
    return down + up + [9.75] * 10, vols


def test_entry_fires_only_with_spike_inside_window():
    c, v = _setup()
    day = _day(c, v)
    rules = TraderRules(window=(900, 1000), require_pvt_green=False)
    assert entries(day, indicators(day), rules).size >= 1
    weak = _day(*_setup(spike=1.5))
    assert entries(weak, indicators(weak), rules).size == 0
    outside = TraderRules(window=(570, 630), require_pvt_green=False)
    assert entries(day, indicators(day), outside).size == 0


def test_first_fade_exit_and_next_open_fill():
    c, v = _setup()
    day = _day(c, v)
    ind = indicators(day)
    t = int(entries(day, ind, TraderRules(window=(900, 1000), require_pvt_green=False))[0])
    f = trade(day, ind, t, len(c) - 1)
    assert f.reason in {"macd_fade", "rsi_back_below_70", "volume_exhaustion", "ema9_below_24"}
    assert f.exit == day["open"].iloc[f.exit_i] and f.exit_i > f.entry_i
