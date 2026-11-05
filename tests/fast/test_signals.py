from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sgt.fast import signals

T0 = pd.Timestamp("2026-11-05T14:30:00Z")        # 09:30 ET (EST, UTC-5): these bars derive segment "rth"


def frame(closes, highs=None, lows=None, opens=None, vol=1000.0):
    n = len(closes)
    opens = list(opens) if opens is not None else list(closes)
    highs = list(highs) if highs is not None else [max(o, c) for o, c in zip(opens, closes)]
    lows = list(lows) if lows is not None else [min(o, c) for o, c in zip(opens, closes)]
    return pd.DataFrame({"ts": [T0 + pd.Timedelta(minutes=i) for i in range(n)], "open": opens,
                         "high": highs, "low": lows, "close": list(closes), "volume": [vol] * n})


def at_end(b: pd.DataFrame) -> pd.Timestamp:
    return b.ts.iloc[-1] + pd.Timedelta(minutes=1)


def test_flat_bars_have_zero_distortion_and_undefined_rsi():
    b = frame([10.0] * 30, highs=[10.0] * 30, lows=[10.0] * 30)
    s = signals.snapshot(b, at_end(b), prev_close=9.5, prior_day_volume=60_000.0)
    assert s["ema9_21_gap_pct"] == pytest.approx(0.0)
    assert s["vwap_dist_pct"] == pytest.approx(0.0)
    assert s["off_hod_pct"] == pytest.approx(0.0)
    assert s["atr14_1m_pct"] == pytest.approx(0.0)
    assert s["rsi14"] is None                                   # no down moves: Wilder RSI undefined
    assert s["rvol_5m"] == pytest.approx(1.0)
    assert s["cum_vol_vs_prev_day"] == pytest.approx(30 * 1000.0 / 60_000.0)
    assert s["gap_pct"] == pytest.approx((10.0 - 9.5) / 9.5)
    assert s["pvt_trend"] == -1.0
    assert s["above_premarket_high"] is None                    # RTH-only bars: no pre-market reference
    assert s["minutes_since_open"] == pytest.approx(30.0)


def test_snapshot_is_causal_appending_future_bars_cannot_change_it():
    rng = np.random.default_rng(7)
    closes = list(10 + np.cumsum(rng.normal(0, 0.05, 80)))
    full = frame(closes)
    for k in (20, 45, 70):
        at = at_end(full.iloc[:k])
        early = signals.snapshot(full.iloc[:k], at, prev_close=10.0, prior_day_volume=5e5)
        late = signals.snapshot(full, at, prev_close=10.0, prior_day_volume=5e5)
        assert early == late                                    # later bars are filtered out at `at`


def test_bars_ending_after_at_are_excluded():
    b = frame([10.0] * 10)
    at = T0 + pd.Timedelta(minutes=5)          # only bars 0..3 ended by 14:35 (bar 4 ends exactly at 14:35)
    s = signals.snapshot(b, at, prev_close=10.0)
    # bar k ends at T0+(k+1)min; <= at keeps k=0..4 -> minutes_since_open = 5
    assert s["minutes_since_open"] == pytest.approx(5.0)


def test_ema_cross_state_and_minutes_since_cross():
    down = list(np.linspace(10.0, 8.0, 20))                     # 9 settles below 21
    up = list(np.linspace(8.0, 12.0, 25))                       # rising: 9 crosses above 21
    b = frame(down + up)
    s = signals.snapshot(b, at_end(b))
    assert s["ema_cross_state"] == 1.0
    assert s["minutes_since_cross"] is not None and 0.0 < s["minutes_since_cross"] < 45.0
    # early, before any rise, 9 is below 21
    early = signals.snapshot(b.iloc[:20], at_end(b.iloc[:20]))
    assert early["ema_cross_state"] == -1.0


def test_psar_side_flips_on_a_v_shaped_series():
    down = list(np.linspace(12.0, 8.0, 25))
    up = list(np.linspace(8.0, 13.0, 25))
    b = frame(down + up)
    bottom = b.iloc[:26]                                        # descending leg
    assert signals.snapshot(bottom, at_end(bottom))["psar_side"] == -1.0
    assert signals.snapshot(b, at_end(b))["psar_side"] == 1.0


def test_vwap_distance_sign_tracks_price_vs_session_vwap():
    b = frame([10.0] * 10 + [11.0] * 10)                        # late bars above the running VWAP
    s = signals.snapshot(b, at_end(b))
    assert s["vwap_dist_pct"] is not None and s["vwap_dist_pct"] > 0


def test_empty_and_pre_only_cases():
    assert all(v is None for v in signals.snapshot(frame([]), T0).values())
    pre = frame([10.0] * 5)
    pre["segment"] = "pre"
    s = signals.snapshot(pre.assign(high=[10.0, 11.0, 10.0, 10.0, 10.5]),
                         at_end(pre), prev_close=10.0)
    assert s["vwap_dist_pct"] is None and s["minutes_since_open"] is None    # no RTH bar yet
    assert s["above_premarket_high"] == 0.0                                  # last close 10 < pre high 11


def test_series_is_per_bar_and_warms_up_to_none():
    b = frame(list(np.linspace(10.0, 11.0, 40)))
    out = signals.series(b)
    assert set(out) == {"rsi", "macd", "signal", "hist"}
    assert all(len(out[k]) == len(b) for k in out)
    assert out["rsi"][0] is None                                # first bar: no prior close, RSI undefined
    assert out["macd"][-1] is not None
