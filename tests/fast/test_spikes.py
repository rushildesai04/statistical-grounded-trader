from __future__ import annotations

import pandas as pd

from sgt.fast import spikes

T0 = pd.Timestamp("2026-03-02T14:30:00Z")          # 09:30 ET


def frame(closes, vols, highs=None):
    n = len(closes)
    return pd.DataFrame({"ts": [T0 + pd.Timedelta(minutes=i) for i in range(n)], "open": closes, "close": closes,
                         "high": highs or closes, "low": closes, "volume": vols})


def test_detects_first_bar_meeting_runup_volume_and_new_high():
    closes = [2.0] * 20 + [2.02, 2.04, 2.06, 2.08, 2.12] + [2.0] * 10
    vols = [1000.0] * 24 + [6000.0] + [1000.0] * 10
    assert spikes.detect(frame(closes, vols)) == 24             # 2.12 / 2.0 = +6%, 6x volume, new high


def test_no_spike_when_any_condition_fails():
    closes = [2.0] * 20 + [2.02, 2.04, 2.06, 2.08, 2.12] + [2.0] * 10
    assert spikes.detect(frame(closes, [1000.0] * 24 + [4000.0] + [1000.0] * 10)) is None      # 4x volume
    assert spikes.detect(frame([2.0] * 20 + [2.01] * 5 + [2.0] * 10, [1000.0] * 24 + [9000.0] + [1000.0] * 10)) is None
    highs = [2.0] * 3 + [2.5] + [2.0] * 31                                             # earlier high 2.50 not beaten
    assert spikes.detect(frame(closes, [1000.0] * 24 + [6000.0] + [1000.0] * 10, highs)) is None
    assert spikes.detect(frame(closes, [1000.0] * 24 + [6000.0] + [1000.0] * 10, highs), need_hod=False) == 24
    cheap = [c / 4 for c in closes]                                                    # $0.53: below $1
    assert spikes.detect(frame(cheap, [1000.0] * 24 + [6000.0] + [1000.0] * 10)) is None


def test_future_bars_do_not_change_detection_and_entry_window_is_respected():
    closes = [2.0] * 20 + [2.02, 2.04, 2.06, 2.08, 2.12]
    vols = [1000.0] * 24 + [6000.0]
    assert spikes.detect(frame(closes + [2.0], vols + [1000.0])) == 24
    assert spikes.detect(frame(closes + [9.0] * 5, vols + [99999.0] * 5)) == 24        # later bars irrelevant
    early = frame([2.0] * 3 + [2.2] * 3, [1000.0] * 5 + [9000.0])                     # entry 09:36 needs 10 base bars
    assert spikes.detect(early) is None
