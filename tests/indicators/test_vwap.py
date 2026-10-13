"""VWAP: session vs rth anchor, resets, zero-volume guard, streaming==batch."""
from __future__ import annotations

from datetime import date

import pytest

from sgt.core.interfaces import IndicatorSpec
from sgt.core.types import Segment
from sgt.indicators import build, batch, frame_from_bars

from _helpers import assert_stream_eq_batch, mk_bar, stream_rows

D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)


def test_session_vwap_resets_each_day():
    bars = [
        mk_bar(10.0, i=0, high=10.0, low=10.0, volume=100, minute=570, session=D1),
        mk_bar(20.0, i=1, high=20.0, low=20.0, volume=100, minute=571, session=D1),
        mk_bar(30.0, i=2, high=30.0, low=30.0, volume=100, minute=570, session=D2),
    ]
    ind = build(IndicatorSpec.of("vwap", anchor="session"))
    rows = stream_rows(ind, bars)
    assert rows[0]["value"] == pytest.approx(10.0)
    assert rows[1]["value"] == pytest.approx(15.0)  # (10+20)/2 equal volume
    assert rows[2]["value"] == pytest.approx(30.0)  # reset on new session
    assert_stream_eq_batch(rows, batch(ind.spec, frame_from_bars(bars)), ("value",))


def test_session_vwap_zero_volume_none():
    bars = [mk_bar(10.0, i=0, high=10.0, low=10.0, volume=0, minute=570, session=D1)]
    ind = build(IndicatorSpec.of("vwap", anchor="session"))
    rows = stream_rows(ind, bars)
    assert rows[0]["value"] is None


def test_rth_vwap_none_before_rth_resets_at_rth():
    bars = [
        mk_bar(10.0, i=0, high=10.0, low=10.0, volume=100, minute=300, session=D1, segment=Segment.PRE),
        mk_bar(50.0, i=1, high=50.0, low=50.0, volume=100, minute=570, session=D1, segment=Segment.RTH),
        mk_bar(60.0, i=2, high=60.0, low=60.0, volume=100, minute=571, session=D1, segment=Segment.RTH),
        mk_bar(90.0, i=3, high=90.0, low=90.0, volume=100, minute=960, session=D1, segment=Segment.POST),
    ]
    ind = build(IndicatorSpec.of("vwap", anchor="rth"))
    rows = stream_rows(ind, bars)
    assert rows[0]["value"] is None  # pre-market
    assert rows[1]["value"] == pytest.approx(50.0)  # anchor resets at first RTH bar
    assert rows[2]["value"] == pytest.approx(55.0)
    assert rows[3]["value"] == pytest.approx(55.0)  # post carries RTH value, no post volume added
    assert_stream_eq_batch(rows, batch(ind.spec, frame_from_bars(bars)), ("value",))
