"""Fade filter v1 features (src/sgt/fast/meta.py): strictly point in time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sgt.fast import meta

ET = ZoneInfo("America/New_York")
T0 = pd.Timestamp(datetime(2026, 1, 5, 9, 30, tzinfo=ET)).tz_convert(timezone.utc)


def bars(closes, vols=None, segment="rth"):
    n = len(closes)
    return pd.DataFrame({"ts": [T0 + timedelta(minutes=i) for i in range(n)], "open": closes, "high": closes,
                         "low": closes, "close": closes, "volume": vols or [100.0] * n, "segment": segment})


def test_features_ignore_bars_that_had_not_ended_at_the_alert():
    px = [10.0] * 30 + [20.0] * 30                      # price doubles at 10:00
    at = T0 + timedelta(minutes=30)                      # alert at 10:00:00: the 10:00 bar has not ended
    f = meta.features(at, "volume_spike", bars(px), bars([400.0] * 60), prev_events=2)
    assert f["f_r_open"] == 0.0 and f["f_logpx"] == pytest.approx(np.log(10.0))
    later = meta.features(at + timedelta(seconds=61), "volume_spike", bars(px), bars([400.0] * 60), 2)
    assert later["f_r_open"] == pytest.approx(1.0) and later["f_r5"] == pytest.approx(1.0)
    assert f["f_minute"] == 30.0 and f["f_type"] == float(meta.TYPES.index("volume_spike")) and f["f_prev_events"] == 2


def test_volume_feature_is_a_ratio_and_premarket_is_ignored():
    b = bars([10.0] * 20, vols=[100.0] * 15 + [300.0] * 5)
    pre = bars([99.0] * 5, segment="pre")
    f = meta.features(T0 + timedelta(minutes=20), "news", pd.concat([pre, b]), b, 0)
    assert f["f_vol5_rel"] == pytest.approx(300 / 150) and f["f_logpx"] == pytest.approx(np.log(10.0))
    empty = meta.features(T0, "news", b, b, 0)                                  # first minute: no bar yet
    assert np.isnan(empty["f_r_open"]) and np.isnan(empty["f_spy_open"])


def test_prev_event_counts_use_only_earlier_sessions():
    ev = pd.DataFrame({"symbol": ["A", "A", "B", "A"], "session": ["d1", "d2", "d2", "d3"],
                       "posted_at": pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-02", "2026-01-03"])})
    assert meta.prev_event_counts(ev).tolist() == [0, 1, 0, 2]


def test_take_policy():
    ins = np.arange(100.0)
    assert meta.take(np.array([10.0, 60.0]), ins, 0.0).all()
    assert meta.take(np.array([10.0, 60.0]), ins, 0.5).tolist() == [False, True]
