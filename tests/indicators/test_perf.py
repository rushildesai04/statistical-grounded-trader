"""Performance sanity: 50k bars through a ~12-indicator bank in < 5 s."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from sgt.core.interfaces import IndicatorSpec
from sgt.core.types import Bar, Segment
from sgt.indicators import IndicatorBank

RTH_START = 570  # 09:30 ET minute-of-day
RTH_LEN = 390


def _make_bars(n: int) -> list[Bar]:
    rng = np.random.default_rng(3)
    base = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
    rets = rng.standard_normal(n) * 0.002
    closes = 5.0 * np.exp(np.cumsum(rets))
    vols = np.abs(rng.standard_normal(n)) * 5000.0 + 1000.0
    bars: list[Bar] = []
    sess0 = date(2026, 1, 5)
    for i in range(n):
        c = float(closes[i])
        o = float(closes[i - 1]) if i else c
        h = max(o, c) * (1.0 + abs(float(rng.standard_normal())) * 0.001)
        lo = min(o, c) * (1.0 - abs(float(rng.standard_normal())) * 0.001)
        day = i // RTH_LEN
        minute = RTH_START + (i % RTH_LEN)
        bars.append(Bar(
            symbol="PERF", ts=base + timedelta(minutes=i), open=round(o, 4), high=round(h, 4),
            low=round(lo, 4), close=round(c, 4), volume=round(float(vols[i])),
            session_date=sess0 + timedelta(days=day), minute_of_day=minute, segment=Segment.RTH,
        ))
    return bars


SPECS = [
    IndicatorSpec.of("ema", n=9),
    IndicatorSpec.of("ema", n=21),
    IndicatorSpec.of("sma", n=50),
    IndicatorSpec.of("macd", fast=12, slow=26, signal=9),
    IndicatorSpec.of("rsi", n=14),
    IndicatorSpec.of("atr", n=14),
    IndicatorSpec.of("adx", n=14),
    IndicatorSpec.of("psar", start=0.02, step=0.02, max=0.2),
    IndicatorSpec.of("supertrend", n=10, mult=3.0),
    IndicatorSpec.of("vwap", anchor="session"),
    IndicatorSpec.of("pvt", signal_n=14, signal_kind="sma"),
    IndicatorSpec.of("relvol", lookback_sessions=20),
]


def test_bank_throughput_50k():
    bars = _make_bars(50_000)
    bank = IndicatorBank(SPECS)
    t0 = time.perf_counter()
    for b in bars:
        bank.update(b)
    elapsed = time.perf_counter() - t0
    assert bank.snapshot()  # exercised at the end
    assert elapsed < 5.0, f"50k bars x {len(SPECS)} indicators took {elapsed:.2f}s"
