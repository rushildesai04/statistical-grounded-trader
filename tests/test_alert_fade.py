"""Short-cover rule and verdict checks for the pre-registered alert fade (scripts/alert_fade_test.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import alert_fade_test as af  # noqa: E402


def w(rows: list[tuple[float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["open", "high", "close"])


def test_cover_at_window_close_without_stop_hit():
    assert af.cover(w([(10, 11, 10.5), (10.5, 11.5, 9)]), 10.0) == 9


def test_stop_covers_at_level_or_gap_open():
    assert af.cover(w([(10, 11, 10.5), (10.5, 12.4, 12)]), 10.0) == pytest.approx(12.0)   # level
    assert af.cover(w([(10, 11, 10.5), (14, 15, 14.5)]), 10.0) == 14                       # halt gap: pay open
    assert af.cover(w([(10, 11, 10.5), (14, 15, 14.5)]), 10.0, stop=None) == 14.5


def test_empty_window_is_missing():
    assert af.cover(w([]), 10.0) is None


def test_verdict_requires_both_halves_and_breadth():
    days = [f"2026-07-{d:02d}" for d in range(1, 29)] * 10
    t = pd.DataFrame({"session": sorted(days), "net": 0.01})
    assert af.verdict(t, np.random.default_rng(0))["pass"]
    t.loc[: len(t) // 2 - 1, "net"] = -0.001                      # first half negative
    assert not af.verdict(t, np.random.default_rng(0))["pass"]
    assert not af.verdict(t.iloc[:150].assign(net=0.01), np.random.default_rng(0))["pass"]   # n < 200
