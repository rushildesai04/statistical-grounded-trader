"""Point-in-time "in play" day detection from daily bars (H2, 2026-10-18).

The trader's setups are stocks gapping on news with extreme volume: a stock is in play on
the DAY it gaps, not on every day after it once moved. A symbol-day qualifies when the
official open gaps up at least ``min_gap`` over the previous session's close and the open
price is inside the tradeable band. Both inputs are known at the opening print, so a
dataset or live scanner gated on this set admits candidates only from the open onward
(no look-ahead: the day's high, close and volume are never used for selection).

Pure functions over validated daily rows; I/O lives in ``scripts/scan_gappers.py``.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True, slots=True)
class DailyBar:
    session: date
    open: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class GapDay:
    symbol: str
    session: date
    prev_close: float
    open: float
    gap_pct: float          # (open / prev_close - 1) * 100
    prev_volume: float


def parse_daily_rows(rows: Iterable[Mapping[str, Any]]) -> list[DailyBar]:
    """Strictly parse Alpaca v2 daily bar rows; malformed rows are dropped."""
    out: list[DailyBar] = []
    for r in rows:
        try:
            ts = str(r["t"])
            o, c, v = float(r["o"]), float(r["c"]), float(r["v"])
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(x) for x in (o, c, v)) or o <= 0 or c <= 0 or v < 0:
            continue
        try:
            d = date.fromisoformat(ts[:10])
        except ValueError:
            continue
        out.append(DailyBar(d, o, c, v))
    out.sort(key=lambda b: b.session)
    return out


def find_gap_days(symbol: str, bars: Sequence[DailyBar], *, min_gap: float = 0.10,
                  price_min: float = 1.0, price_max: float = 30.0,
                  start: date | None = None) -> list[GapDay]:
    """Symbol-days whose open gaps >= ``min_gap`` above the PREVIOUS session close.

    ``bars`` must be consecutive trading sessions (Alpaca daily bars skip only
    non-trading days, and a missing day for a halted symbol makes the comparison span
    two sessions, which is still the last known close at the open)."""
    out: list[GapDay] = []
    for prev, cur in zip(bars, bars[1:]):
        if start is not None and cur.session < start:
            continue
        if not (price_min <= cur.open <= price_max):
            continue
        gap = cur.open / prev.close - 1.0
        if gap >= min_gap:
            out.append(GapDay(symbol.upper(), cur.session, prev.close, cur.open,
                              round(100.0 * gap, 4), prev.volume))
    return out
