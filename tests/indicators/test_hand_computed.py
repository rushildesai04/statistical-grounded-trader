"""Hand-computed golden values for the core math (tiny series, exact numbers)."""
from __future__ import annotations

import math

import pytest

from sgt.core.interfaces import IndicatorSpec
from sgt.indicators import build, batch, frame_from_bars

from _helpers import assert_stream_eq_batch, closes_to_bars, stream_rows


def _vals(kind, closes, volumes=None, **params):
    bars = closes_to_bars(closes, volumes)
    ind = build(IndicatorSpec.of(kind, **params))
    rows = stream_rows(ind, bars)
    return bars, ind, rows


def test_sma_exact():
    bars, ind, rows = _vals("sma", [1, 2, 3, 4, 5], n=3)
    got = [r["value"] for r in rows]
    assert got[:2] == [None, None]
    assert got[2:] == pytest.approx([2.0, 3.0, 4.0])
    assert_stream_eq_batch(rows, batch(ind.spec, frame_from_bars(bars)), ("value",))


def test_ema_exact():
    bars, ind, rows = _vals("ema", [1, 2, 3, 4, 5], n=3)
    got = [r["value"] for r in rows]
    assert got[0] is None and got[1] is None
    assert got[2:] == pytest.approx([2.0, 3.0, 4.0])


def test_ema1_is_close():
    _, _, rows = _vals("ema", [3, 7, 2, 9], n=1)
    assert [r["value"] for r in rows] == pytest.approx([3, 7, 2, 9])


def test_rsi_exact_and_edges():
    # changes +1,+1,-1,0 with n=2
    bars, ind, rows = _vals("rsi", [1, 2, 3, 2, 2], n=2)
    got = [r["value"] for r in rows]
    assert got[:2] == [None, None]
    assert got[2] == pytest.approx(100.0)  # all gains
    assert got[3] == pytest.approx(50.0)
    assert got[4] == pytest.approx(50.0)


def test_rsi_flat_is_50():
    _, _, rows = _vals("rsi", [5, 5, 5, 5], n=2)
    assert rows[-1]["value"] == pytest.approx(50.0)


def test_pvt_exact():
    bars, ind, rows = _vals("pvt", [10, 11, 10.5], volumes=[100, 200, 300], signal_n=2, signal_kind="sma")
    pvt = [r["pvt"] for r in rows]
    assert pvt[0] == pytest.approx(0.0)
    assert pvt[1] == pytest.approx(20.0)
    assert pvt[2] == pytest.approx(20.0 - 150.0 / 11.0)
    sig = [r["signal"] for r in rows]
    assert sig[0] is None
    assert sig[1] == pytest.approx(10.0)
    assert sig[2] == pytest.approx((20.0 + (20.0 - 150.0 / 11.0)) / 2.0)


def test_macd_exact():
    bars, ind, rows = _vals("macd", [1, 2, 3, 4, 5, 6], fast=2, slow=3, signal=2)
    line = [r["line"] for r in rows]
    sig = [r["signal"] for r in rows]
    hist = [r["hist"] for r in rows]
    assert line[:2] == [None, None]
    assert line[2:] == pytest.approx([0.5, 0.5, 0.5, 0.5])
    assert sig[:3] == [None, None, None]
    assert sig[3:] == pytest.approx([0.5, 0.5, 0.5])
    assert hist[3:] == pytest.approx([0.0, 0.0, 0.0])
    assert_stream_eq_batch(rows, batch(ind.spec, frame_from_bars(bars)), ("line", "signal", "hist"))


def test_roc_exact():
    bars, ind, rows = _vals("roc", [10, 11, 12, 13], n=2)
    got = [r["value"] for r in rows]
    assert got[:2] == [None, None]
    assert got[2] == pytest.approx(20.0)
    assert got[3] == pytest.approx(2.0 / 11.0 * 100.0)
