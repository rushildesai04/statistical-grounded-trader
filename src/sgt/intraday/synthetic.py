"""Deterministic synthetic 1-minute sessions for tests, demos and engine soak runs.

Not market data and never written to the market store. Generates log-normal
price paths with optional momentum "ignition" episodes (volume spike + drift)
so strategy/engine tests exercise realistic crossover/volume behavior without
network access.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from sgt.core.types import Bar, Segment

ET = ZoneInfo("America/New_York")

_SEGMENT_BOUNDS = {
    Segment.PRE: (time(4, 0), time(9, 30)),
    Segment.RTH: (time(9, 30), time(16, 0)),
    Segment.POST: (time(16, 0), time(20, 0)),
}


@dataclass(frozen=True)
class Ignition:
    """A momentum episode: from ``start_minute`` (minutes after the first bar)
    price drifts by ``drift_per_bar`` (log) for ``length`` bars with volume
    multiplied by ``volume_mult``; then reverses at ``-fade`` drift for ``length`` bars."""

    start_minute: int
    length: int = 20
    drift_per_bar: float = 0.01
    volume_mult: float = 8.0
    fade: float = 0.006


def segment_for(et_time: time) -> Segment | None:
    for seg, (lo, hi) in _SEGMENT_BOUNDS.items():
        if lo <= et_time < hi:
            return seg
    return None


def make_session_bars(
    symbol: str,
    session_date: date,
    *,
    start_price: float = 5.0,
    base_volume: float = 20_000.0,
    vol_per_bar: float = 0.004,
    segments: tuple[Segment, ...] = (Segment.RTH,),
    ignitions: tuple[Ignition, ...] = (),
    seed: int = 0,
) -> list[Bar]:
    """Bars for one session over the requested segments, in time order."""
    rng = np.random.default_rng(seed)
    bars: list[Bar] = []
    price = start_price
    k = 0
    for seg in sorted(segments, key=lambda s: _SEGMENT_BOUNDS[s][0]):
        lo, hi = _SEGMENT_BOUNDS[seg]
        t = datetime.combine(session_date, lo, tzinfo=ET)
        end = datetime.combine(session_date, hi, tzinfo=ET)
        while t < end:
            drift, vmult = 0.0, 1.0
            for ig in ignitions:
                rel = k - ig.start_minute
                if 0 <= rel < ig.length:
                    drift, vmult = ig.drift_per_bar, ig.volume_mult
                elif ig.length <= rel < 2 * ig.length:
                    drift, vmult = -ig.fade, max(1.0, ig.volume_mult / 3)
            seg_vol_mult = 0.25 if seg is not Segment.RTH else 1.0
            ret = drift + vol_per_bar * rng.standard_normal()
            o = price
            c = max(0.01, o * math.exp(ret))
            wick = abs(vol_per_bar * rng.standard_normal()) * o
            h = max(o, c) + wick
            lw = min(o, c) - abs(vol_per_bar * rng.standard_normal()) * o
            low = max(0.005, lw)
            v = float(max(0.0, base_volume * seg_vol_mult * vmult * rng.lognormal(0.0, 0.5)))
            ts_utc = t.astimezone(timezone.utc)
            bars.append(Bar(
                symbol=symbol, ts=ts_utc, open=round(o, 4), high=round(h, 4), low=round(low, 4),
                close=round(c, 4), volume=round(v), session_date=session_date,
                minute_of_day=t.hour * 60 + t.minute, segment=seg,
            ))
            # keep OHLC consistent after rounding
            b = bars[-1]
            if b.high < max(b.open, b.close) or b.low > min(b.open, b.close):
                bars[-1] = Bar(symbol=symbol, ts=ts_utc, open=b.open, high=max(b.open, b.close, b.high),
                               low=min(b.open, b.close, b.low), close=b.close, volume=b.volume,
                               session_date=session_date, minute_of_day=b.minute_of_day, segment=seg)
            price = c
            k += 1
            t += timedelta(minutes=1)
    return bars


def make_multi_session(symbol: str, session_dates: list[date], *, seed: int = 0, **kw) -> list[Bar]:
    """Consecutive sessions; each session's open continues from the prior close."""
    out: list[Bar] = []
    price = kw.pop("start_price", 5.0)
    for i, d in enumerate(session_dates):
        bars = make_session_bars(symbol, d, start_price=price, seed=seed + i, **kw)
        out.extend(bars)
        if bars:
            price = bars[-1].close
    return out
