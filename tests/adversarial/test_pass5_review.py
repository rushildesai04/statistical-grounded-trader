"""Adversarial pass 5 (independent review of 028f034..cac8cee). Read-only against production code."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

import sms_control as sc
from sgt.fast import fade
from sgt.intraday.calendar import default_calendar
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")
HALF = date(2026, 11, 27)        # Friday after Thanksgiving: 13:00 ET close
HOLIDAY = date(2026, 11, 26)     # Thanksgiving


def test_calendar_facts():
    cal = default_calendar()
    assert cal.bounds(HALF).rth_close.astimezone(ET).time() == time(13, 0)
    assert not cal.is_session(HOLIDAY)


@pytest.mark.parametrize("stamp", [
    "2026-11-10T20:50:24.123456+00:00",     # old live.decide format: datetime.now(timezone.utc).isoformat()
    "2026-11-10T20:50:24Z",
    "2026-11-10T15:50:24-05:00",
])
def test_old_written_at_formats_still_book(stamp):
    d = date(2026, 11, 10)
    assert sc.model_fill({"date": d.isoformat(), "mode": "realtime", "written_at": stamp},
                         datetime(2026, 11, 10, 15, 51, tzinfo=ET)) == d


def test_model_fill_holiday_and_half_day_never_raise():
    w = {"mode": "realtime", "written_at": "2026-11-26T20:50:00+00:00"}
    assert sc.model_fill({"date": HOLIDAY.isoformat(), **w}, datetime(2026, 11, 26, 15, 51, tzinfo=ET)) is None
    early = {"date": HALF.isoformat(), "mode": "realtime",
             "written_at": datetime(2026, 11, 27, 12, 50, tzinfo=ET).isoformat()}
    assert sc.model_fill(early, datetime(2026, 11, 27, 12, 51, tzinfo=ET)) == HALF
    assert sc.model_fill(early, datetime(2026, 11, 27, 13, 0, tzinfo=ET)) is None


def test_fade_half_day_window_and_holiday(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    # P5-2: same rule as the research population (entry bar 09:30-15:44 and an RTH bar, i.e. before the close)
    assert fade.last_entry(HALF) == time(12, 59)
    assert fade.last_entry(date(2026, 11, 2)) == time(15, 44)
    at = lambda h, m, s=50: datetime(2026, 11, 27, h, m, s, tzinfo=ET).astimezone(timezone.utc)  # noqa: E731
    ok = fade.LiveAlert("DEF", "volume_spike", at(12, 58, 30), "-1", 3)  # entry bar 12:59: tested, allowed
    assert fade.on_alert(db, ok, now=at(12, 58, 45)) != "filtered: window"
    a = fade.LiveAlert("ABC", "volume_spike", at(12, 59, 30), "-1", 1)   # entry bar 13:00 = the bell
    assert fade.on_alert(db, a, now=at(12, 59, 45)) == "filtered: window"
    h = fade.LiveAlert("XYZ", "volume_spike", datetime(2026, 11, 26, 15, 0, tzinfo=ET), "-1", 2)
    assert fade.on_alert(db, h) == "not a session"


def test_half_day_writes_no_live_decision(tmp_path, monkeypatch):
    """P5-1: on a 13:00 half day the 15:50 cron must not write a realtime decision (the book refuses it but mark()
    would record it, so the two disagreed). No file -> the evening catchup records the day in batch mode (A1),
    which the book never trades: book and forward record agree."""
    import dataclasses

    import megacap_forward as mf

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            t = datetime(2026, 11, 27, 15, 50, 20, tzinfo=ET)
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)

    def no_fetch(*a, **k):
        raise AssertionError("no data fetch after the close")

    monkeypatch.setattr(mf, "datetime", Clock)
    monkeypatch.setattr(mf, "alpaca_bars", no_fetch)
    monkeypatch.setattr(mf, "adjusted_daily", no_fetch)
    cfg = dataclasses.replace(mf.SETS["orig"], root=tmp_path)
    assert cfg.first_decision <= HALF
    assert mf.do_decide(cfg) == 0
    assert not (tmp_path / "decisions" / f"{HALF}.json").exists()
