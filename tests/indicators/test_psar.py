"""PSAR: hand-checked flip + independent reference implementation."""
from __future__ import annotations

import math

import numpy as np
import pytest

from sgt.core.interfaces import IndicatorSpec
from sgt.indicators import build, batch, frame_from_bars

from _helpers import assert_stream_eq_batch, mk_bar, stream_rows


def _bars(hlc):
    out = []
    for i, (h, l, c) in enumerate(hlc):
        out.append(mk_bar(c, i=i, high=h, low=l, open=c, minute=570 + i))
    return out


def reference_psar(high, low, close, start, step, maxaf):
    """Straightforward from-scratch Wilder PSAR, independent of the library."""
    m = len(high)
    sar = [math.nan] * m
    trend = [math.nan] * m
    if m < 2:
        return sar, trend
    up = close[1] > close[0]
    s = low[0] if up else high[0]
    ep = high[1] if up else low[1]
    af = start
    sar[1] = s
    trend[1] = 1.0 if up else -1.0
    for i in range(2, m):
        s = s + af * (ep - s)
        if up:
            s = min(s, low[i - 1], low[i - 2])
            if high[i] > ep:
                ep = high[i]
                af = min(af + step, maxaf)
            if low[i] < s:
                up = False
                s = ep
                ep = low[i]
                af = start
        else:
            s = max(s, high[i - 1], high[i - 2])
            if low[i] < ep:
                ep = low[i]
                af = min(af + step, maxaf)
            if high[i] > s:
                up = True
                s = ep
                ep = high[i]
                af = start
        sar[i] = s
        trend[i] = 1.0 if up else -1.0
    return sar, trend


HLC = [
    (10.0, 9.0, 9.5),
    (11.0, 10.0, 10.5),
    (12.0, 11.0, 11.5),
    (12.5, 11.5, 12.0),
    (11.0, 8.0, 8.5),   # low penetrates SAR -> flip to downtrend
]


def test_psar_hand_flip():
    bars = _bars(HLC)
    ind = build(IndicatorSpec.of("psar", start=0.02, step=0.02, max=0.2))
    rows = stream_rows(ind, bars)
    assert rows[0]["sar"] is None
    assert rows[1]["sar"] == pytest.approx(9.0) and rows[1]["trend"] == 1.0
    assert rows[2]["sar"] == pytest.approx(9.0) and rows[2]["trend"] == 1.0
    assert rows[3]["sar"] == pytest.approx(9.12) and rows[3]["trend"] == 1.0
    # flip: SAR becomes prior EP (12.5), trend -1
    assert rows[4]["trend"] == -1.0
    assert rows[4]["sar"] == pytest.approx(12.5)


def test_psar_matches_reference_and_batch():
    rng = np.random.default_rng(7)
    n = 300
    close = 5.0 + np.cumsum(rng.standard_normal(n) * 0.05)
    close = np.abs(close) + 1.0
    high = close + np.abs(rng.standard_normal(n) * 0.03) + 0.01
    low = close - np.abs(rng.standard_normal(n) * 0.03) - 0.01
    low = np.clip(low, 0.01, None)
    hlc = list(zip(high.tolist(), low.tolist(), close.tolist()))
    bars = _bars(hlc)
    for start, step, mx in [(0.02, 0.02, 0.2), (0.01, 0.01, 0.19)]:
        spec = IndicatorSpec.of("psar", start=start, step=step, max=mx)
        ind = build(spec)
        rows = stream_rows(ind, bars)
        ref_sar, ref_trend = reference_psar(
            [b.high for b in bars], [b.low for b in bars], [b.close for b in bars], start, step, mx
        )
        for i in range(len(bars)):
            if math.isnan(ref_sar[i]):
                assert rows[i]["sar"] is None
            else:
                assert rows[i]["sar"] == pytest.approx(ref_sar[i], rel=1e-12)
                assert rows[i]["trend"] == ref_trend[i]
        assert_stream_eq_batch(rows, batch(spec, frame_from_bars(bars)), ("sar", "trend"))
