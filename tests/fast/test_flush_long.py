"""Drop detector and long exit of prereg_flush_long_2026-11-10."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from flush_long_study import long_exit  # noqa: E402

from sgt.fast import spikes  # noqa: E402

T0 = pd.Timestamp("2026-03-03 15:00", tz="UTC")             # 10:00 ET


def day(closes, vols, lows=None):
    lows = lows or [c * 0.995 for c in closes]
    return pd.DataFrame({"ts": [T0 + pd.Timedelta(minutes=i) for i in range(len(closes))], "open": closes,
                         "high": [c * 1.005 for c in closes], "low": lows, "close": closes, "volume": vols})


BASE = [10.0] * 25


def test_drop_detected_at_the_flush_bar_and_never_on_a_spike():
    closes = BASE + [9.9, 9.8, 9.7, 9.6, 9.4] + [9.5] * 10        # -6% in 5 bars at index 29, new low, burst
    vols = [1000] * 29 + [8000] + [1000] * 10
    assert spikes.detect_drop(day(closes, vols)) == 29
    assert spikes.detect(day(closes, vols)) is None
    up = BASE + [10.1, 10.2, 10.3, 10.4, 10.6] + [10.5] * 10
    assert spikes.detect_drop(day(up, vols)) is None


def test_drop_needs_volume_new_low_and_size():
    closes = BASE + [9.9, 9.8, 9.7, 9.6, 9.4] + [9.5] * 10
    assert spikes.detect_drop(day(closes, [1000] * 40)) is None                         # no burst
    small = BASE + [9.95, 9.9, 9.85, 9.8, 9.6] + [9.6] * 10                            # -4%
    assert spikes.detect_drop(day(small, [1000] * 29 + [8000] + [1000] * 10)) is None
    old_low = [10.0] * 10 + [9.0] + [10.0] * 14 + [9.9, 9.8, 9.7, 9.6, 9.4] + [9.5] * 10   # 9.0 earlier: no new low
    assert spikes.detect_drop(day(old_low, [1000] * 29 + [8000] + [1000] * 10)) is None
    assert spikes.detect_drop(day(old_low, [1000] * 29 + [8000] + [1000] * 10), need_lod=False) == 29


def test_long_exit_stop_and_time():
    rth = day([10.0, 10.2, 7.5, 7.0] + [8.0] * 70, [1] * 74, lows=[9.9, 10.0, 7.4, 6.9] + [7.9] * 70)
    assert long_exit(rth, T0, 10.0) == (7.5, "stop")          # bar opens at 7.50, below the 8.00 stop: gap fill
    rth = day([10.0, 10.2, 8.5, 8.4] + [9.0] * 70, [1] * 74, lows=[9.9, 10.0, 7.9, 8.3] + [8.9] * 70)
    assert long_exit(rth, T0, 10.0) == (8.0, "stop")          # trades through 8.00 inside the bar: the level
    rth = day([10.0] * 30 + [10.3] * 50, [1] * 80)
    assert long_exit(rth, T0, 10.0) == (10.3, "time")          # close of the 60th minute
    assert long_exit(rth, T0, 10.0, hold=15) == (10.0, "time")
