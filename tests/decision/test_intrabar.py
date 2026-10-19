"""Intrabar replay: provisional-candle trigger, fake-outs count, latency, clean prints."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.decision.intrabar import bar_state, clean_trades, fill_after, first_trigger


def _day():
    c = np.r_[np.linspace(10.0, 9.0, 40)]
    return pd.DataFrame({"open": c, "high": c + .01, "low": c - .01, "close": c,
                         "volume": np.full(len(c), 1000.0)})


def _ticks(prices, sizes, t0=0):
    return np.array([(t0 + i * 1e9, p, s) for i, (p, s) in enumerate(zip(prices, sizes))], float)


def test_trigger_needs_cross_and_spike_and_happens_mid_minute():
    st = bar_state(_day(), 40, prior_gap_pct=0.005, lookback=15, spike_lookback=5)
    assert st is not None and st.was_below
    kw = dict(vol_spike=5.0, rsi_max=101, require_macd_pos=False, require_pvt_green=False)
    # price jumps on small size first (no spike yet), then size floods in
    ticks = _ticks([9.0, 10.6, 10.7, 10.7, 10.75], [100, 100, 100, 6000, 100])
    k = first_trigger(st, ticks, **kw)
    assert k == 3                               # first tick with cross AND >=5x volume
    assert first_trigger(st, _ticks([9.0, 9.01], [9000, 9000]), **kw) is None   # no cross


def test_fill_respects_latency():
    ticks = _ticks([1, 2, 3, 4], [1, 1, 1, 1])
    assert fill_after(ticks, 0, 1.5) == 2
    assert fill_after(ticks, 3, 1.0) is None


def test_clean_trades_drops_non_last_sale_prints():
    rows = [{"t": "2024-10-02T13:30:01Z", "p": 10, "s": 100, "c": ["@"]},
            {"t": "2024-10-02T13:30:00Z", "p": 9, "s": 50, "c": ["I"]},          # odd lot
            {"t": "2024-10-02T13:30:02Z", "p": 11, "s": 100, "c": ["@", "Q"]},   # official open
            {"t": "bad", "p": 1, "s": 1}]
    out = clean_trades(rows)
    assert out.shape == (1, 3) and out[0, 1] == 10
