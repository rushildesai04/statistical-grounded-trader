"""Simulator for the pre-registered QQQ 0DTE option-call test (scripts/options_ideas_test.py)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import options_ideas_test as oit  # noqa: E402

from sgt.signals_live.ideas import OptionIdea  # noqa: E402

POSTED = pd.Timestamp("2026-03-03 14:40:20", tz="UTC")  # 09:40:20 ET -> live from 09:41
COST = 2 * oit.SPREAD_SIDE + 2 * oit.FEE_CONTRACT_SIDE / 100


def frame(rows, start="2026-03-03 14:41"):
    """rows: list of (open, high, low, close) per minute from ``start`` (UTC)."""
    f = pd.DataFrame(rows, columns=["open", "high", "low", "close"])
    f.insert(0, "ts", pd.date_range(start, periods=len(rows), freq="min", tz="UTC"))
    return f


def test_occ_symbol_formats_half_strikes():
    idea = OptionIdea("QQQ", "P", 0, 492.5, 1.2, 0.5)
    assert oit.occ_symbol(idea, date(2024, 10, 23)) == "QQQ241023P00492500"
    assert oit.occ_symbol(OptionIdea("QQQ", "C", 0, 722.0, 3.0, 0.5), date(2026, 10, 28)) == "QQQ261028C00722000"


def test_no_fill_when_price_stays_above_limit():
    f = frame([(1.10, 1.12, 1.06, 1.10)] * 12)            # limit = 1.05
    assert oit.simulate(1.00, POSTED, f)["status"] == "no_fill"


def test_bar_before_live_window_is_not_a_fill():
    f = frame([(1.0, 1.0, 1.0, 1.0)] + [(1.2, 1.2, 1.2, 1.2)] * 15, start="2026-03-03 14:40")
    assert oit.simulate(1.00, POSTED, f)["status"] == "no_fill"   # 14:40 bar starts before 09:41 ET


def test_posted_plan_two_legs_take_profit():
    # fill 1.00; leg A +20% at 1.20, leg B +100% at 2.00
    f = frame([(1.00, 1.00, 1.00, 1.00), (1.10, 1.25, 1.05, 1.2), (1.5, 2.10, 1.4, 2.0)])
    r = oit.simulate(1.00, POSTED, f)
    assert r["status"] == "filled" and r["fill"] == pytest.approx(1.00)
    assert r["R1"] == pytest.approx((1.6 - 1.0 - COST) / 1.0)
    assert r["R2"] == pytest.approx((1.2 - 1.0 - COST) / 1.0)


def test_stop_counts_first_when_bar_touches_both():
    f = frame([(1.00, 1.00, 1.00, 1.00), (1.00, 1.30, 0.40, 0.5)])
    r = oit.simulate(1.00, POSTED, f)
    assert r["R1"] == pytest.approx((0.50 - 1.0 - COST) / 1.0)   # stop at 0.50, not TP
    assert r["R3"] == pytest.approx(r["R1"])


def test_stop_gap_fills_at_open_below_stop():
    f = frame([(1.00, 1.00, 1.00, 1.00), (0.30, 0.35, 0.20, 0.25)])
    assert oit.simulate(1.00, POSTED, f)["R1"] == pytest.approx((0.30 - 1.0 - COST) / 1.0)


def test_unfilled_legs_exit_at_1550_open():
    # fill at 09:41 ET; drift to 15:50 ET (14:41 UTC + 369 min), never touching TP or stop
    rows = [(1.00, 1.00, 1.00, 1.00)] + [(1.10, 1.15, 0.9, 1.1)] * 368 + [(0.80, 0.8, 0.8, 0.8)] * 3
    f = frame(rows)
    r = oit.simulate(1.00, POSTED, f)
    assert r["R3"] == pytest.approx((0.80 - 1.0 - COST) / 1.0)


def test_cost_doubling_and_fill_capped_at_limit():
    f = frame([(1.20, 1.20, 1.04, 1.1), (1.1, 1.1, 1.1, 1.1)])   # opens above limit, trades down to it
    r = oit.simulate(1.00, POSTED, f)
    assert r["fill"] == pytest.approx(1.05)
    assert r["R3_2x"] == pytest.approx(r["R3"] - COST / 1.05)


def test_missing_bars_is_no_bars():
    assert oit.simulate(1.0, POSTED, pd.DataFrame(columns=["ts", "open", "high", "low", "close"]))["status"] == "no_bars"
