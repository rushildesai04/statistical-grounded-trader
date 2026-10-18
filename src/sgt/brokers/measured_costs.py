"""Measured execution-cost model built from real Alpaca SIP NBBO quotes.

:class:`MeasuredCostModel` is drop-in compatible with :class:`sgt.brokers.costs.CostModel`
as the simulation engine uses it (``fees``, ``max_fill_qty``, ``slippage``,
``half_spread_pct``), but derives the half-spread from measured spreads
(``spread_samples``, written by ``scripts/measure_spreads.py``) when a symbol and
timestamp are supplied. It falls back to the bucket model (flagged) when fewer than
``min_samples`` measured windows exist for the nearest time-of-day bucket.

Point-in-time: a lookup for ``ts`` uses ONLY samples from sessions strictly before
``ts``'s session. There is no look-ahead. ``stress(mult)`` returns a copy with every
spread scaled by ``mult`` (for the mandated +-50% shock, F1 in docs/DECISION_MODEL.md).

Why an optional ``symbol``/``ts`` path instead of a new interface: the engine's
``SimBroker`` calls ``slippage(price, side, qty, bar_volume)`` with no symbol, so the
measured lookup is opt-in via keyword and those calls transparently use the bucket
fallback. Nothing in the existing call sites changes.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from statistics import median
from typing import Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

from sgt.brokers.costs import DEFAULT_COST_MODEL, CostModel
from sgt.core.types import Side
from sgt.intraday.calendar import SessionCalendar, default_calendar
from sgt.intraday.quotes import SpreadSample, SpreadSampleRow, TOD_BUCKETS, bucket_center, tod_bucket

_UTC = timezone.utc
_ET = ZoneInfo("America/New_York")

SOURCE_MEASURED = "measured"
SOURCE_FALLBACK = "model_fallback"


class MeasuredCostModel:
    """Cost model whose half-spread is measured per symbol x time-of-day bucket."""

    def __init__(
        self,
        samples: Iterable[SpreadSample],
        *,
        base: CostModel = DEFAULT_COST_MODEL,
        min_samples: int = 3,
        stress_mult: float = 1.0,
        calendar: SessionCalendar | None = None,
    ) -> None:
        if min_samples < 1:
            raise ValueError("min_samples must be >= 1")
        if stress_mult <= 0:
            raise ValueError("stress_mult must be > 0")
        self._base = base
        self._min_samples = min_samples
        self._stress_mult = float(stress_mult)
        self._cal = calendar or default_calendar()
        self._samples: tuple[SpreadSample, ...] = tuple(samples)
        self._by_symbol: dict[str, list[SpreadSample]] = defaultdict(list)
        for s in self._samples:
            self._by_symbol[s.symbol.upper()].append(s)

    # -- construction -------------------------------------------------------
    @classmethod
    def from_db(
        cls,
        session_factory: Callable[[], object],
        *,
        base: CostModel = DEFAULT_COST_MODEL,
        min_samples: int = 3,
        calendar: SessionCalendar | None = None,
    ) -> "MeasuredCostModel":
        """Load every persisted spread sample into memory. ``session_factory`` is the
        repo's ``get_session`` context manager."""
        samples: list[SpreadSample] = []
        with session_factory() as session:  # type: ignore[attr-defined]
            for r in session.query(SpreadSampleRow).all():
                samples.append(SpreadSample(
                    symbol=r.symbol,
                    session_date=date.fromisoformat(r.session_date),
                    minute_of_day=r.minute_of_day,
                    segment=r.segment,
                    rel_spread_median=r.rel_spread_median,
                    n_quotes=r.n_quotes,
                    mid_price=0.0,  # not persisted; unused for lookups
                ))
        return cls(samples, base=base, min_samples=min_samples, calendar=calendar)

    # -- measured lookup ----------------------------------------------------
    def _query_session_minute(self, ts: datetime) -> tuple[date, int]:
        if ts.tzinfo is None:
            raise ValueError("ts must be tz-aware")
        qdate = self._cal.session_of(ts) or ts.astimezone(_ET).date()
        et = ts.astimezone(_ET)
        return qdate, et.hour * 60 + et.minute

    def _measured_half_spread(self, symbol: str, ts: datetime) -> float | None:
        """0.5 x median measured relative spread for the nearest time-of-day bucket,
        using only sessions strictly before ``ts``'s session. ``None`` when no bucket
        reaches ``min_samples``."""
        samples = self._by_symbol.get(symbol.upper())
        if not samples:
            return None
        qdate, qmod = self._query_session_minute(ts)
        qbucket = tod_bucket(qmod)
        by_bucket: dict[str, list[float]] = defaultdict(list)
        for s in samples:
            if s.session_date < qdate:
                by_bucket[tod_bucket(s.minute_of_day)].append(s.rel_spread_median)
        if not by_bucket:
            return None
        others = sorted((b for b in TOD_BUCKETS if b != qbucket),
                        key=lambda b: abs(bucket_center(b) - qmod))
        for bucket in (qbucket, *others):
            vals = by_bucket.get(bucket)
            if vals and len(vals) >= self._min_samples:
                return 0.5 * median(vals)
        return None

    # -- CostModel-compatible surface ---------------------------------------
    def half_spread_pct(
        self, price: float, *, symbol: str | None = None, ts: datetime | None = None
    ) -> float:
        """Half-spread fraction of price. Measured when ``symbol``/``ts`` resolve to
        enough samples, else the bucket estimate. Always scaled by the stress multiplier."""
        hs: float | None = None
        if symbol is not None and ts is not None:
            hs = self._measured_half_spread(symbol, ts)
        if hs is None:
            hs = self._base.half_spread_pct(price)
        return hs * self._stress_mult

    def round_trip(self, symbol: str, ts: datetime, price: float) -> tuple[float, str]:
        """Round-trip cost fraction (enter + exit half-spreads) and its source.

        ``("measured"|"model_fallback")``. Stress is applied to both sources so a
        shocked run moves measured and fallback costs together.
        """
        if price <= 0:
            raise ValueError("round_trip price must be > 0")
        hs = self._measured_half_spread(symbol, ts)
        if hs is not None:
            source = SOURCE_MEASURED
        else:
            hs = self._base.half_spread_pct(price)
            source = SOURCE_FALLBACK
        return 2.0 * hs * self._stress_mult, source

    def slippage(
        self,
        price: float,
        side: Side,
        qty: int,
        bar_volume: float,
        free_float: float | None = None,
        measured_spread_pct: float | None = None,
        *,
        symbol: str | None = None,
        ts: datetime | None = None,
    ) -> float:
        """Adverse per-fill price adjustment, delegating the float/participation
        multipliers to the base model but substituting the measured half-spread."""
        hs = measured_spread_pct
        if hs is None:
            hs = self.half_spread_pct(price, symbol=symbol, ts=ts)
        return self._base.slippage(
            price, side, qty, bar_volume, free_float=free_float, measured_spread_pct=hs
        )

    def max_fill_qty(self, bar_volume: float) -> int:
        return self._base.max_fill_qty(bar_volume)

    def fees(self, qty: int) -> float:
        return self._base.fees(qty)

    # -- stress -------------------------------------------------------------
    def stress(self, mult: float) -> "MeasuredCostModel":
        """A copy with all spreads scaled by ``mult`` (compounds with any prior stress)."""
        if mult <= 0:
            raise ValueError("stress mult must be > 0")
        return MeasuredCostModel(
            self._samples,
            base=self._base,
            min_samples=self._min_samples,
            stress_mult=self._stress_mult * mult,
            calendar=self._cal,
        )

    # -- reporting ----------------------------------------------------------
    @property
    def stress_mult(self) -> float:
        return self._stress_mult

    @property
    def min_samples(self) -> int:
        return self._min_samples

    def coverage(self) -> dict[str, int]:
        """Sample count per symbol (diagnostics)."""
        return {sym: len(v) for sym, v in self._by_symbol.items()}
