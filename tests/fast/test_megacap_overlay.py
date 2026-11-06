from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import megacap_overlay as mo  # noqa: E402


def test_overlay_holds_except_on_short_calls_and_pays_the_round_trip():
    pos, ret = pd.Series([1, 0, -1, -1]), pd.Series([0.01, -0.02, -0.03, 0.02])
    assert mo.overlay(pos, ret).tolist() == pytest.approx([0.01, -0.02, -0.0004, -0.0004])


def test_max_drawdown_and_summary():
    r = pd.Series([0.1, -0.5, 0.2])
    assert mo.max_dd(r) == pytest.approx(-0.5)
    s = mo.summary(r, mo.overlay(pd.Series([0, -1, 0]), r))
    assert s["aside_share"] == pytest.approx(1 / 3) and s["overlay_maxdd"] > s["hold_maxdd"]
