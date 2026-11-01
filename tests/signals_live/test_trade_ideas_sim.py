"""Simulator for the pre-registered trade-ideas test (scripts/trade_ideas_test.py)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import trade_ideas_test as tit  # noqa: E402

from sgt.signals_live.ideas import StockIdea  # noqa: E402

IDEA = StockIdea("ABCD", 10.0, (0.05, 0.10, 0.20), 8.5, False)
POSTED = pd.Timestamp("2026-03-03 14:40:20", tz="UTC")  # 09:40:20 ET


def frame(rows, start="2026-03-03 14:30"):
    """rows: list of (open, high, low, close) from 09:30 ET, one per minute."""
    ts = pd.date_range(start, periods=len(rows), freq="min", tz="UTC")
    f = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    f.insert(0, "ts", ts)
    et = f.ts.dt.tz_convert("America/New_York")
    f["tod"] = et.dt.hour * 60 + et.dt.minute
    return f


def flat(n, px=10.2):
    return [(px, px, px, px)] * n


def test_fill_then_take_profits():
    rows = flat(11) + [(10.0, 10.0, 9.98, 10.0)] + flat(5, 10.3) + [(10.4, 10.6, 10.4, 10.5)] + [(11, 11.1, 11, 11)] + [(12, 12.1, 12, 12)]
    r = tit.simulate(IDEA, POSTED, frame(rows))
    assert r["status"] == "filled" and r["fill"] == pytest.approx(10.0)   # min(limit 10.05, open 10.0)
    # floor(2000 / 10.05) = 199 shares -> legs 67/66/66; TPs 10.5, 11.0, 12.0
    assert r["gross"] == pytest.approx((67 * 0.5 + 66 * 1.0 + 66 * 2.0) / (199 * 10.0))
    assert r["net"] == pytest.approx(r["gross"] - r["cost"]) and r["green5"]


def test_bar_touching_tp_and_stop_counts_as_stop():
    rows = flat(11) + [(10.0, 10.0, 9.98, 10.0)] + [(10.0, 10.6, 8.4, 9.0)] + flat(3)
    r = tit.simulate(IDEA, POSTED, frame(rows))
    assert r["stopped"] and r["gross"] == pytest.approx(8.5 / 10.0 - 1)


def test_gap_down_stop_fills_at_open():
    rows = flat(11) + [(10.0, 10.0, 9.98, 10.0)] + [(8.0, 8.1, 7.9, 8.0)]
    assert tit.simulate(IDEA, POSTED, frame(rows))["gross"] == pytest.approx(-0.2)


def test_eod_exit_at_1555_open():
    rows = flat(11) + [(10.0, 10.0, 9.98, 10.0)] + flat(373, 10.2) + [(10.3, 10.3, 10.3, 10.3)]
    r = tit.simulate(IDEA, POSTED, frame(rows))
    assert frame(rows).tod.iloc[-1] == 15 * 60 + 55
    assert r["gross"] == pytest.approx(0.03)


def test_no_fill_and_window_is_10_minutes():
    rows = flat(21) + [(9.9, 9.9, 9.9, 9.9)] * 3   # dips below limit only at 09:51 (> posted + 10 min)
    assert tit.simulate(IDEA, POSTED, frame(rows))["status"] == "no_fill"


def test_fill_bar_never_before_posting():
    rows = flat(10) + [(9.0, 9.0, 9.0, 9.0)] + flat(10)   # 09:40 bar dips, but the call came at 09:40:20
    assert tit.simulate(IDEA, POSTED, frame(rows))["status"] == "no_fill"


def test_runaway_skip():
    rows = [(10.5, 10.5, 10.5, 10.5)] * 12
    r = tit.simulate(IDEA, POSTED, frame(rows))
    assert r["status"] == "skip" and "already ran" in r["reason"]


def test_no_bars():
    assert tit.simulate(IDEA, POSTED, None)["status"] == "no_bars"
