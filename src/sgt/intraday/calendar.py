"""US-equities session calendar (XNYS), the single source of truth for session
boundaries, named windows and bar annotation.

Backed by ``exchange_calendars`` ("XNYS") for holidays/early-closes and the
regular-trading-hours (RTH) open/close. Pre-market (04:00 ET) and post-market
(20:00 ET, or 17:00 ET on early-close days) are overlaid on top because xcals
only models RTH. All boundaries are tz-aware UTC; ET is used only internally and
is DST-correct via ``zoneinfo``.

Every bar in the system is stamped by :meth:`SessionCalendar.annotate` so all
consumers agree on ``session_date``, ``minute_of_day`` and ``Segment``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
from exchange_calendars.errors import DateOutOfBounds
import pandas as pd

from sgt.core.types import Bar, Segment

ET = ZoneInfo("America/New_York")
_UTC = timezone.utc

_PRE_OPEN_ET = time(4, 0)
_POST_CLOSE_NORMAL_ET = time(20, 0)
_POST_CLOSE_EARLY_ET = time(17, 0)
_REGULAR_CLOSE_ET = time(16, 0)
_MIDDAY_START_ET = time(11, 0)
_MIDDAY_END_ET = time(14, 0)
_OPENING_WINDOW_MIN = 60
_POWER_HOUR_MIN = 60

# Named windows produced by :meth:`SessionCalendar.windows`.
WIN_PRE = "pre"
WIN_RTH = "rth"
WIN_POST = "post"
WIN_AFTER_HOURS = "after_hours"  # alias of post
WIN_OPENING_HOUR = "opening_hour"
WIN_MIDDAY = "midday"
WIN_POWER_HOUR = "power_hour"


@dataclass(frozen=True, slots=True)
class SessionBounds:
    """The four UTC boundaries of one trading session.

    ``pre_open`` 04:00 ET, ``rth_open``/``rth_close`` from the exchange calendar
    (early closes handled), ``post_close`` 20:00 ET (17:00 ET on early-close days).
    """

    session_date: date
    pre_open: datetime
    rth_open: datetime
    rth_close: datetime
    post_close: datetime
    early_close: bool

    def __post_init__(self) -> None:
        for name in ("pre_open", "rth_open", "rth_close", "post_close"):
            ts = getattr(self, name)
            if ts.tzinfo is None or ts.utcoffset() != timezone.utc.utcoffset(None):
                raise ValueError(f"SessionBounds.{name} must be tz-aware UTC, got {ts!r}")
        if not (self.pre_open <= self.rth_open < self.rth_close <= self.post_close):
            raise ValueError(f"SessionBounds ordering invalid: {self}")


def _et_to_utc(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=ET).astimezone(_UTC)


class SessionCalendar:
    """Deterministic, DST-correct session calendar for US equities.

    Pure: no I/O after construction, no mutable state beyond per-date caches.
    """

    def __init__(self, name: str = "XNYS") -> None:
        self.name = name
        self._cal = xcals.get_calendar(name)

    # -- session membership -------------------------------------------------
    def is_session(self, d: date) -> bool:
        try:
            return bool(self._cal.is_session(pd.Timestamp(d)))
        except DateOutOfBounds:
            # Outside the calendar's known bounds: not a tradeable session.
            return False

    @lru_cache(maxsize=4096)  # noqa: B019 - instance lives process-long; bounded size
    def bounds(self, d: date) -> SessionBounds:
        """UTC boundaries for session ``d``. Raises ValueError if ``d`` is not a session."""
        if not self.is_session(d):
            raise ValueError(f"{d} is not an {self.name} session")
        ts = pd.Timestamp(d)
        rth_open = self._cal.session_open(ts).to_pydatetime().astimezone(_UTC)
        rth_close = self._cal.session_close(ts).to_pydatetime().astimezone(_UTC)
        early = rth_close.astimezone(ET).time() < _REGULAR_CLOSE_ET
        post_t = _POST_CLOSE_EARLY_ET if early else _POST_CLOSE_NORMAL_ET
        return SessionBounds(
            session_date=d,
            pre_open=_et_to_utc(d, _PRE_OPEN_ET),
            rth_open=rth_open,
            rth_close=rth_close,
            post_close=_et_to_utc(d, post_t),
            early_close=early,
        )

    # -- annotation ---------------------------------------------------------
    def session_of(self, ts: datetime) -> date | None:
        """The session date ``ts`` belongs to, or None if it is outside 04:00-post_close
        on a session day. ``ts`` must be tz-aware."""
        if ts.tzinfo is None:
            raise ValueError("session_of requires a tz-aware datetime")
        ts = ts.astimezone(_UTC)
        d = ts.astimezone(ET).date()
        if not self.is_session(d):
            return None
        b = self.bounds(d)
        return d if b.pre_open <= ts < b.post_close else None

    def segment(self, ts: datetime) -> Segment | None:
        d = self.session_of(ts)
        if d is None:
            return None
        ts = ts.astimezone(_UTC)
        b = self.bounds(d)
        if b.pre_open <= ts < b.rth_open:
            return Segment.PRE
        if b.rth_open <= ts < b.rth_close:
            return Segment.RTH
        return Segment.POST  # rth_close <= ts < post_close

    def annotate(
        self,
        symbol: str,
        ts_utc: datetime,
        o: float,
        h: float,
        l: float,
        c: float,
        v: float,
        vwap: float | None = None,
        trade_count: int | None = None,
    ) -> Bar | None:
        """Build an annotated :class:`Bar`, or None if ``ts_utc`` is outside a session
        window (04:00-post_close on a trading day)."""
        if ts_utc.tzinfo is None:
            raise ValueError("annotate requires a tz-aware ts_utc")
        ts_utc = ts_utc.astimezone(_UTC)
        d = self.session_of(ts_utc)
        if d is None:
            return None
        seg = self.segment(ts_utc)
        assert seg is not None  # session_of guaranteed a valid window
        et = ts_utc.astimezone(ET)
        return Bar(
            symbol=symbol,
            ts=ts_utc,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=v,
            session_date=d,
            minute_of_day=et.hour * 60 + et.minute,
            segment=seg,
            vwap=vwap,
            trade_count=trade_count,
        )

    # -- named windows ------------------------------------------------------
    def windows(self, ts: datetime) -> frozenset[str]:
        """Named windows ``ts`` falls in. Empty if ``ts`` is outside any session."""
        d = self.session_of(ts)
        if d is None:
            return frozenset()
        ts = ts.astimezone(_UTC)
        b = self.bounds(d)
        seg = self.segment(ts)
        out: set[str] = set()
        if seg is Segment.PRE:
            out.add(WIN_PRE)
        elif seg is Segment.RTH:
            out.add(WIN_RTH)
            if (ts - b.rth_open).total_seconds() < _OPENING_WINDOW_MIN * 60:
                out.add(WIN_OPENING_HOUR)
            if (b.rth_close - ts).total_seconds() <= _POWER_HOUR_MIN * 60:
                out.add(WIN_POWER_HOUR)
            et_t = ts.astimezone(ET).time()
            if _MIDDAY_START_ET <= et_t < _MIDDAY_END_ET:
                out.add(WIN_MIDDAY)
        elif seg is Segment.POST:
            out.add(WIN_POST)
            out.add(WIN_AFTER_HOURS)
        return frozenset(out)

    # -- navigation ---------------------------------------------------------
    def previous_sessions(self, d: date, n: int) -> list[date]:
        """The ``n`` session dates strictly before ``d`` (``d`` need not be a session),
        oldest first."""
        if n <= 0:
            return []
        cur = pd.Timestamp(d)
        out: list[date] = []
        for _ in range(n):
            cur = self._cal.previous_session(cur)
            out.append(cur.date())
        return list(reversed(out))


@lru_cache(maxsize=4)
def default_calendar() -> SessionCalendar:
    """Process-wide cached XNYS calendar."""
    return SessionCalendar("XNYS")
