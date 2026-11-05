from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sgt.crypto import pumpfade


def frame(closes, start="2024-01-01", highs=None):
    ts = pd.date_range(start, periods=len(closes), freq="1min", tz="UTC")
    c = np.asarray(closes, float)
    return pd.DataFrame({"ts": ts, "open": c, "high": c if highs is None else np.asarray(highs, float),
                         "low": c, "close": c, "volume": 1.0})


def test_run_up_uses_last_candle_at_or_before_30_min_and_skips_missing_minutes():
    f = frame([100.0] * 40 + [110.0])
    f = f.drop(index=range(5, 15))                                          # gap: no trades for 10 minutes
    r = pumpfade.run_up(f)
    assert r.iloc[-1] == pytest.approx(0.10)


def test_events_need_full_window_floor_and_excess_over_btc_and_respect_gap():
    n = 60 * 24 * 3
    base = 100 * (1 + 0.0001 * np.sin(np.arange(n)))                        # calm coin
    coin = base.copy()
    coin[n - 600:] *= 1.06                                                  # +6% burst, then flat (one event)
    coin[n - 300:] *= 1.06                                                  # second burst inside 6 h: skipped
    c, btc = frame(coin), frame(base)
    ev = pumpfade.events(c, btc, window=60 * 24)
    assert len(ev) == 1 and ev.run.iloc[0] >= 0.05
    assert pumpfade.events(c, btc, window=60 * 24 * 10).empty               # no full window yet
    market = frame(coin)                                                    # BTC bursts too: not coin-specific
    assert pumpfade.events(c, market, window=60 * 24).empty
    assert len(pumpfade.events(c, market, window=60 * 24, min_excess=None)) == 1


def test_trade_short_returns_stop_and_gap():
    f = frame([100.0] * 5 + [100.0, 98.0, 95.0, 96.0])
    ev = pd.DataFrame({"ts": [f.ts.iloc[4]], "run": [0.05]})
    t = pumpfade.trade(f, ev, hold=4)
    assert t.entry.iloc[0] == 100 and t.cover.iloc[0] == 96 and not t.stopped.iloc[0]
    assert t.net.iloc[0] == pytest.approx(0.04 - pumpfade.COST)
    s = pumpfade.trade(frame([100.0] * 5 + [100.0, 101.0], highs=[100.0] * 5 + [100.0, 115.0]), ev, hold=4)
    assert s.stopped.iloc[0] and s.cover.iloc[0] == pytest.approx(110.0)       # bar opened 101 < stop: fill at stop
    g = pumpfade.trade(frame([100.0] * 5 + [100.0, 130.0]), ev, hold=4)
    assert g.cover.iloc[0] == 130.0                                         # gapped through the stop: its open


def test_long_side_mirrors_returns_and_stops_on_lows():
    ev = pd.DataFrame({"ts": [pd.Timestamp("2024-01-01 00:04", tz="UTC")], "run": [0.05]})
    up = pumpfade.trade(frame([100.0] * 5 + [100.0, 104.0]), ev, hold=4, side=1)
    assert up.gross.iloc[0] == pytest.approx(0.04) and not up.stopped.iloc[0]
    lows = frame([100.0] * 5 + [100.0, 95.0])
    lows.loc[6, "low"] = 85.0                                              # wick through the 10% stop
    s = pumpfade.trade(lows, ev, hold=4, side=1)
    assert s.stopped.iloc[0] and s.cover.iloc[0] == pytest.approx(90.0)
    with pytest.raises(ValueError):
        pumpfade.trade(lows, ev, side=0)
