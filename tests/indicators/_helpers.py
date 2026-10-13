"""Shared helpers for indicator tests (no DB, no network)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np

from sgt.core.types import Bar, Segment

BASE = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)


def mk_bar(
    close: float,
    *,
    i: int = 0,
    open: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1000.0,
    minute: int = 570,
    session: date = date(2026, 1, 5),
    segment: Segment = Segment.RTH,
    symbol: str = "TEST",
) -> Bar:
    o = close if open is None else open
    h = max(o, close) if high is None else high
    lo = min(o, close) if low is None else low
    ts = BASE + timedelta(minutes=i)
    return Bar(
        symbol=symbol, ts=ts, open=o, high=h, low=lo, close=close, volume=volume,
        session_date=session, minute_of_day=minute, segment=segment,
    )


def closes_to_bars(closes, volumes=None) -> list[Bar]:
    vols = volumes if volumes is not None else [1000.0] * len(closes)
    return [mk_bar(c, i=i, volume=v, minute=570 + i) for i, (c, v) in enumerate(zip(closes, vols))]


def stream_rows(ind, bars) -> list[dict]:
    rows = []
    for b in bars:
        ind.update(b)
        rows.append(dict(ind.values()))
    return rows


def _col(rows, name) -> np.ndarray:
    return np.array([np.nan if r[name] is None else r[name] for r in rows], dtype=float)


def assert_stream_eq_batch(rows, df, outputs, *, rtol=1e-9) -> None:
    for o in outputs:
        s = _col(rows, o)
        b = df[o].to_numpy(dtype=float)
        assert np.array_equal(np.isnan(s), np.isnan(b)), f"{o}: None/NaN positions differ"
        mask = ~np.isnan(s)
        assert np.allclose(s[mask], b[mask], rtol=rtol, atol=0.0), (
            f"{o}: values differ (max rel {np.max(np.abs((s[mask]-b[mask])/b[mask])) if mask.any() else 0})"
        )
