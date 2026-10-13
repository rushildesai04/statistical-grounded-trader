"""Relative volume (no look-ahead) and float rotation."""
from __future__ import annotations

from datetime import date

import pytest

from sgt.core.interfaces import IndicatorSpec, ReferenceData
from sgt.indicators import IndicatorContext, build, batch, frame_from_bars

from _helpers import mk_bar, stream_rows

D1 = date(2026, 1, 5)
D2 = date(2026, 1, 6)
D3 = date(2026, 1, 7)


def _session(session, minute_vols, start_i):
    return [
        mk_bar(10.0, i=start_i + k, high=10.0, low=10.0, volume=v, minute=570 + m, session=session)
        for k, (m, v) in enumerate(minute_vols)
    ]


def test_relvol_first_session_none_then_ratio():
    # session1 minute 0 vol 100, minute 1 vol 200
    s1 = _session(D1, [(0, 100), (1, 200)], 0)
    # session2 minute 0 vol 300, minute 1 vol 100
    s2 = _session(D2, [(0, 300), (1, 100)], 10)
    bars = s1 + s2
    ind = build(IndicatorSpec.of("relvol", lookback_sessions=20))
    rows = stream_rows(ind, bars)
    assert rows[0]["value"] is None and rows[1]["value"] is None  # no prior session
    assert rows[2]["value"] == pytest.approx(3.0)  # 300 / 100
    assert rows[3]["value"] == pytest.approx(0.5)  # 100 / 200
    # cum: session2 minute0 cum=300 vs prior cum at minute0 =100 -> 3.0
    assert rows[2]["cum"] == pytest.approx(3.0)
    # session2 minute1 cum=400 vs prior cum at minute1 = 300 -> 1.3333
    assert rows[3]["cum"] == pytest.approx(400.0 / 300.0)


def test_relvol_no_lookahead_batch():
    s1 = _session(D1, [(0, 100), (1, 100)], 0)
    s2 = _session(D2, [(0, 200), (1, 200)], 10)
    s3 = _session(D3, [(0, 999), (1, 999)], 20)
    spec = IndicatorSpec.of("relvol", lookback_sessions=20)
    df_full = batch(spec, frame_from_bars(s1 + s2 + s3))
    # Mutate the FUTURE session (s3) volumes; earlier rows must not change.
    s3b = _session(D3, [(0, 1), (1, 1)], 20)
    df_mut = batch(spec, frame_from_bars(s1 + s2 + s3b))
    before = df_full.iloc[:4].to_numpy()
    after = df_mut.iloc[:4].to_numpy()
    import numpy as np

    assert np.array_equal(np.isnan(before), np.isnan(after))
    m = ~np.isnan(before)
    assert np.allclose(before[m], after[m])


def test_relvol_streaming_eq_batch():
    s1 = _session(D1, [(0, 100), (1, 150)], 0)
    s2 = _session(D2, [(0, 200), (1, 250)], 10)
    s3 = _session(D3, [(0, 300), (1, 50)], 20)
    bars = s1 + s2 + s3
    spec = IndicatorSpec.of("relvol", lookback_sessions=2)
    ind = build(spec)
    rows = stream_rows(ind, bars)
    from _helpers import assert_stream_eq_batch

    assert_stream_eq_batch(rows, batch(spec, frame_from_bars(bars)), ("value", "cum"))


def test_float_rotation_with_and_without_float():
    bars = [
        mk_bar(10.0, i=0, volume=100_000, minute=570, session=D1),
        mk_bar(10.0, i=1, volume=150_000, minute=571, session=D1),
        mk_bar(10.0, i=2, volume=50_000, minute=570, session=D2),  # new session resets cum
    ]
    ref = ReferenceData(symbol="TEST", as_of=D1, free_float=1_000_000.0)
    ctx = IndicatorContext(reference=ref)
    ind = build(IndicatorSpec.of("float_rotation"), ctx)
    rows = stream_rows(ind, bars)
    assert ind.ready
    assert rows[0]["value"] == pytest.approx(0.1)
    assert rows[1]["value"] == pytest.approx(0.25)
    assert rows[2]["value"] == pytest.approx(0.05)
    from _helpers import assert_stream_eq_batch

    assert_stream_eq_batch(rows, batch(ind.spec, frame_from_bars(bars), ctx), ("value",))

    # unknown float -> always None, not ready
    ind2 = build(IndicatorSpec.of("float_rotation"), IndicatorContext(reference=None))
    rows2 = stream_rows(ind2, bars)
    assert not ind2.ready
    assert all(r["value"] is None for r in rows2)
