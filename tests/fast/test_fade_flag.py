"""Cover rule of prereg_fade_flag_2026-11-10 (X1): the fade's 20% stop plus a buy-stop at the flag high."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from fade_flag_study import cover_with_flag  # noqa: E402

T0 = pd.Timestamp("2026-11-09 15:00", tz="UTC")


def w(rows):
    return pd.DataFrame([{"ts": T0 + pd.Timedelta(minutes=i), "open": o, "high": h, "low": lo, "close": c}
                         for i, (o, h, lo, c) in enumerate(rows)])


FLAG = {"flag_hi": 10.5, "ready": T0 + pd.Timedelta(minutes=2)}


def test_no_flag_is_the_plain_fade():
    bars = w([(10, 10.2, 9.8, 10.0), (10.0, 10.1, 9.0, 9.1)])
    assert cover_with_flag(bars, 10.0, None) == (9.1, False)


def test_breakout_before_ready_does_not_count_then_covers_at_the_level():
    bars = w([(10, 10.6, 9.9, 10.1), (10.1, 10.2, 9.9, 10.0), (10.0, 10.7, 9.9, 10.6), (10.6, 11.0, 10.5, 10.9)])
    assert cover_with_flag(bars, 10.0, FLAG) == (10.5, True)


def test_gap_over_the_level_covers_at_the_open_and_stop_still_rules_above():
    bars = w([(10, 10.1, 9.9, 10.0), (10.0, 10.1, 9.9, 10.0), (10.8, 10.9, 10.7, 10.8)])
    assert cover_with_flag(bars, 10.0, FLAG) == (10.8, True)
    high_flag = {"flag_hi": 12.5, "ready": T0}                 # flag high above the 20% stop: the stop rules
    assert cover_with_flag(w([(10, 12.2, 9.9, 12.1)]), 10.0, high_flag) == (12.0, False)
