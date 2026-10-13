"""SessionCalendar: DST, early closes, holidays, window edges, annotation."""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from sgt.core.types import Segment
from sgt.intraday.calendar import (
    SessionCalendar,
    default_calendar,
)

ET = ZoneInfo("America/New_York")
UTC = timezone.utc


@pytest.fixture(scope="module")
def cal() -> SessionCalendar:
    return default_calendar()


def et(d: date, h: int, m: int) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET).astimezone(UTC)


# --- DST correctness -------------------------------------------------------
def test_march_dst_session_bounds_are_dst_correct(cal):
    # EDT (UTC-4) in effect after 2025-03-09; RTH 09:30 ET == 13:30 UTC
    b = cal.bounds(date(2025, 3, 14))
    assert b.rth_open == datetime(2025, 3, 14, 13, 30, tzinfo=UTC)
    assert b.rth_close == datetime(2025, 3, 14, 20, 0, tzinfo=UTC)
    assert b.pre_open == datetime(2025, 3, 14, 8, 0, tzinfo=UTC)   # 04:00 EDT
    assert b.post_close == datetime(2025, 3, 15, 0, 0, tzinfo=UTC)  # 20:00 EDT
    assert not b.early_close


def test_november_standard_time_session_bounds(cal):
    # EST (UTC-5) after 2025-11-02; RTH 09:30 ET == 14:30 UTC
    b = cal.bounds(date(2025, 11, 7))
    assert b.rth_open == datetime(2025, 11, 7, 14, 30, tzinfo=UTC)
    assert b.rth_close == datetime(2025, 11, 7, 21, 0, tzinfo=UTC)
    assert b.pre_open == datetime(2025, 11, 7, 9, 0, tzinfo=UTC)    # 04:00 EST
    assert b.post_close == datetime(2025, 11, 8, 1, 0, tzinfo=UTC)  # 20:00 EST


# --- early close & holidays ------------------------------------------------
def test_early_close_day(cal):
    # Day after Thanksgiving 2025: close 13:00 ET, post 17:00 ET
    b = cal.bounds(date(2025, 11, 28))
    assert b.early_close
    assert b.rth_close.astimezone(ET).hour == 13
    assert b.post_close.astimezone(ET).hour == 17


def test_holiday_is_not_a_session(cal):
    assert not cal.is_session(date(2025, 12, 25))
    with pytest.raises(ValueError):
        cal.bounds(date(2025, 12, 25))


def test_weekend_is_not_a_session(cal):
    assert not cal.is_session(date(2025, 3, 15))  # Saturday


# --- window membership edges ----------------------------------------------
def test_window_edges_regular_day(cal):
    d = date(2025, 3, 14)
    assert cal.windows(et(d, 9, 30)) == frozenset({"rth", "opening_hour"})
    assert cal.windows(et(d, 10, 29)) == frozenset({"rth", "opening_hour"})
    assert cal.windows(et(d, 10, 30)) == frozenset({"rth"})
    assert cal.windows(et(d, 11, 0)) == frozenset({"rth", "midday"})
    assert cal.windows(et(d, 13, 59)) == frozenset({"rth", "midday"})
    assert cal.windows(et(d, 14, 0)) == frozenset({"rth"})
    assert cal.windows(et(d, 15, 0)) == frozenset({"rth", "power_hour"})
    assert cal.windows(et(d, 15, 59)) == frozenset({"rth", "power_hour"})
    assert cal.windows(et(d, 16, 0)) == frozenset({"post", "after_hours"})
    assert cal.windows(et(d, 5, 0)) == frozenset({"pre"})
    assert cal.windows(et(d, 3, 0)) == frozenset()  # before pre-open


def test_power_hour_is_early_close_aware(cal):
    d = date(2025, 11, 28)  # early close 13:00 ET
    assert "power_hour" in cal.windows(et(d, 12, 30))
    assert "power_hour" in cal.windows(et(d, 12, 59))
    assert cal.windows(et(d, 13, 0)) == frozenset({"post", "after_hours"})


# --- annotation ------------------------------------------------------------
def test_annotate_segments(cal):
    d = date(2025, 3, 14)
    pre = cal.annotate("WFF", et(d, 5, 0), 5.0, 5.1, 4.9, 5.05, 1000.0)
    assert pre is not None and pre.segment is Segment.PRE and pre.minute_of_day == 300
    rth = cal.annotate("WFF", et(d, 9, 30), 5.0, 5.1, 4.9, 5.05, 1000.0)
    assert rth.segment is Segment.RTH and rth.minute_of_day == 570
    post = cal.annotate("WFF", et(d, 18, 0), 5.0, 5.1, 4.9, 5.05, 1000.0)
    assert post.segment is Segment.POST
    assert post.session_date == d


def test_annotate_outside_session_returns_none(cal):
    d = date(2025, 3, 14)
    assert cal.annotate("WFF", et(d, 2, 0), 5, 5, 5, 5, 1) is None  # before 04:00
    assert cal.annotate("WFF", et(d, 20, 0), 5, 5, 5, 5, 1) is None  # at/after post close
    holiday = date(2025, 12, 25)
    assert cal.annotate("WFF", et(holiday, 10, 0), 5, 5, 5, 5, 1) is None


def test_annotate_passes_vwap_and_trade_count(cal):
    d = date(2025, 3, 14)
    bar = cal.annotate("WFF", et(d, 10, 0), 5.0, 5.1, 4.9, 5.05, 1000.0, vwap=5.02, trade_count=7)
    assert bar.vwap == 5.02 and bar.trade_count == 7


def test_session_of_and_previous_sessions(cal):
    d = date(2025, 3, 14)
    assert cal.session_of(et(d, 10, 0)) == d
    assert cal.session_of(et(d, 2, 0)) is None
    assert cal.previous_sessions(d, 3) == [date(2025, 3, 11), date(2025, 3, 12), date(2025, 3, 13)]
    assert cal.previous_sessions(d, 0) == []


def test_naive_datetime_rejected(cal):
    with pytest.raises(ValueError):
        cal.session_of(datetime(2025, 3, 14, 15, 0))
