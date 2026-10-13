"""Operational alert rate-limiting: identical alerts within the window are deduped."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from live_fakes import RecordingNotifier

from sgt.live.alerts import AlertManager


class _Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


async def test_identical_alert_deduped_within_window_and_resent_after():
    start = datetime(2024, 3, 12, 14, 0, tzinfo=timezone.utc)
    clock = _Clock(start)
    rec = RecordingNotifier()
    mgr = AlertManager(rec, window_s=300.0, clock=clock)

    assert mgr.alert("STALE FEED", "no bars for 160s") is True
    assert mgr.alert("STALE FEED", "no bars for 160s") is False  # deduped
    clock.now = start + timedelta(seconds=120)
    assert mgr.alert("STALE FEED", "no bars for 160s") is False  # still within window
    clock.now = start + timedelta(seconds=301)
    assert mgr.alert("STALE FEED", "no bars for 160s") is True   # window lapsed
    assert len(rec.sent) == 2
    assert mgr.suppressed_count == 2


async def test_distinct_alerts_not_deduped():
    rec = RecordingNotifier()
    mgr = AlertManager(rec, window_s=300.0)
    mgr.alert("A", "one")
    mgr.alert("A", "two")
    mgr.alert("B", "one")
    assert len(rec.sent) == 3


async def test_failing_notifier_is_bounded_not_fatal():
    class _Boom:
        def send(self, title, body):
            raise RuntimeError("webhook down")

    mgr = AlertManager(_Boom())
    assert mgr.alert("X", "y") is False
    assert mgr.failed_count == 1
