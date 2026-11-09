from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_passive_exit_study as pe  # noqa: E402

E = pd.Timestamp("2026-11-09 15:00", tz="UTC")                 # 10:00 ET


def bars(lows_after=(9.0, 9.0), highs=10.5, close_after=9.4):
    ts = pd.date_range(E, periods=62, freq="1min", tz="UTC")
    b = pd.DataFrame({"ts": ts, "open": 10.0, "high": highs, "low": 9.5, "close": 9.5})
    b.loc[60:, "low"] = list(lows_after)
    b.loc[60:, "close"] = close_after
    return b


def test_filled_when_a_print_goes_through_the_bid():
    r = pe.simulate(bars(lows_after=(9.4, 9.6)), 10.0, E, mid=9.5, hs=0.01)
    assert r["filled"] is True and r["delta"] == pytest.approx((9.5 * 1.01 - 9.5 * 0.99) / 10.0)


def test_unfilled_pays_the_later_ask():
    r = pe.simulate(bars(lows_after=(9.405, 9.41), close_after=9.8), 10.0, E, mid=9.5, hs=0.01)   # bid 9.405: no print below
    assert r["filled"] is False and r["delta"] == pytest.approx((9.5 * 1.01 - 9.8 * 1.01) / 10.0)


def test_stopped_no_quote_and_near_close_are_unchanged():
    assert pe.simulate(bars(highs=12.0), 10.0, E, 9.5, 0.01)["kind"] == "stopped_or_empty"
    assert pe.simulate(bars(), 10.0, E, None, None)["kind"] == "no_quote"
    late = pd.Timestamp("2026-11-09 19:59", tz="UTC")             # exit 15:59 ET + 2 min > 16:00
    b = bars().assign(ts=pd.date_range(late, periods=62, freq="1min", tz="UTC"))
    assert pe.simulate(b, 10.0, late, 9.5, 0.01) == {"kind": "near_close", "delta": 0.0, "filled": None}
