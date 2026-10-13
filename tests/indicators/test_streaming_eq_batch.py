"""Streaming == batch for EVERY kind over a 3-session multi-session series
(pre/rth/post), plus reset() returns to a fresh state."""
from __future__ import annotations

from datetime import date

import pytest

from sgt.core.interfaces import IndicatorSpec, ReferenceData
from sgt.core.types import Segment
from sgt.indicators import IndicatorContext, KINDS, build, batch, frame_from_bars
from sgt.intraday.synthetic import Ignition, make_multi_session

from _helpers import assert_stream_eq_batch, stream_rows

SESSIONS = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
SEGMENTS = (Segment.PRE, Segment.RTH, Segment.POST)


def _bars():
    return make_multi_session(
        "TEST", SESSIONS, seed=11, segments=SEGMENTS,
        ignitions=(Ignition(start_minute=400, length=15, volume_mult=6.0),),
    )


def _ctx():
    return IndicatorContext(reference=ReferenceData(symbol="TEST", as_of=SESSIONS[0], free_float=5_000_000.0))


# One representative spec per kind (defaults exercised elsewhere).
SPECS = [
    IndicatorSpec.of("sma", n=10),
    IndicatorSpec.of("ema", n=9),
    IndicatorSpec.of("ewo", fast=5, slow=35),
    IndicatorSpec.of("rsi", n=14),
    IndicatorSpec.of("macd", fast=12, slow=26, signal=9),
    IndicatorSpec.of("roc", n=12),
    IndicatorSpec.of("atr", n=14),
    IndicatorSpec.of("bbw", n=20, k=2.0),
    IndicatorSpec.of("psar", start=0.02, step=0.02, max=0.2),
    IndicatorSpec.of("psar", start=0.01, step=0.01, max=0.19),
    IndicatorSpec.of("adx", n=14),
    IndicatorSpec.of("supertrend", n=10, mult=3.0),
    IndicatorSpec.of("cross_strength", fast=9, slow=24, atr_n=14),
    IndicatorSpec.of("pvt", signal_n=14, signal_kind="sma"),
    IndicatorSpec.of("pvt", signal_n=21, signal_kind="ema"),
    IndicatorSpec.of("vwap", anchor="session"),
    IndicatorSpec.of("vwap", anchor="rth"),
    IndicatorSpec.of("relvol", lookback_sessions=2),
    IndicatorSpec.of("float_rotation"),
]


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.key)
def test_streaming_equals_batch(spec):
    bars = _bars()
    ctx = _ctx()
    ind = build(spec, ctx)
    rows = stream_rows(ind, bars)
    df = batch(spec, frame_from_bars(bars), ctx)
    assert list(df.columns) == list(ind.outputs)
    assert len(df) == len(bars)
    assert_stream_eq_batch(rows, df, ind.outputs)


def test_every_kind_has_a_spec():
    covered = {s.kind for s in SPECS}
    assert covered == set(KINDS), f"missing coverage for {set(KINDS) - covered}"


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.key)
def test_reset_returns_fresh_state(spec):
    bars = _bars()
    ctx = _ctx()
    ind = build(spec, ctx)
    rows_first = stream_rows(ind, bars)
    ind.reset()
    # float_rotation's readiness is config-based (free float), so it legitimately
    # survives reset; everything else must be un-ready after reset.
    if spec.kind != "float_rotation":
        assert not ind.ready
    assert all(v is None for v in ind.values().values())
    rows_second = stream_rows(ind, bars)
    for a, b in zip(rows_first, rows_second):
        for o in ind.outputs:
            assert (a[o] is None) == (b[o] is None)
            if a[o] is not None:
                assert a[o] == pytest.approx(b[o], rel=1e-12)
