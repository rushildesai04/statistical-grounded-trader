"""Session-hours watchdog: a silent listener is texted from a separate process (operational risk review, 2026-11-23)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sgt.ops import watchdog as wd
from sgt.ops.store import OpsStore, iso

ET = ZoneInfo("America/New_York")
NOON = datetime(2026, 11, 23, 12, 0, tzinfo=ET).astimezone(timezone.utc)      # a session, alert hours


def beat(db, at):
    db.set_flag("fade_heartbeat", iso(at))


def test_a_listener_that_runs_is_never_texted_about(tmp_path):
    db, sent = OpsStore(tmp_path / "ops.db"), []
    assert wd.check(db, sent.append, NOON) is None           # never ran: the pre-market check's business
    beat(db, NOON - timedelta(seconds=15))
    assert wd.check(db, sent.append, NOON) is None
    beat(db, NOON - wd.STALE)
    assert wd.check(db, sent.append, NOON) is None and sent == []


def test_a_silent_listener_in_alert_hours_is_texted_once_then_every_half_hour_with_what_is_open(tmp_path):
    db, sent = OpsStore(tmp_path / "ops.db"), []
    beat(db, NOON - timedelta(minutes=4))
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, created_at) "
                  "VALUES ('AAA','2026-11-23','new_high','x','-1',1,'open','x')")
    text = wd.check(db, sent.append, NOON)
    assert sent == [text] and "has not run for 4 min (last pass 11:56 ET)" in text
    assert "Open on paper: 1 short, 0 long. Their stops are NOT being checked." in text
    for m in (1, 15, 29):                                    # still down: not again inside the half hour
        assert wd.check(db, sent.append, NOON + timedelta(minutes=m)) is None
    again = wd.check(db, sent.append, NOON + timedelta(minutes=31))
    assert len(sent) == 2 and "has not run for 35 min" in again
    beat(db, NOON + timedelta(minutes=40))                   # back: silent again
    assert wd.check(db, sent.append, NOON + timedelta(minutes=40, seconds=30)) is None and len(sent) == 2


def test_a_warning_that_could_not_be_sent_is_tried_again_the_next_minute(tmp_path):
    db, sent, fail = OpsStore(tmp_path / "ops.db"), [], [True]
    beat(db, NOON - timedelta(minutes=5))

    def send(text):
        if fail[0]:
            raise OSError("the text service is down")
        sent.append(text)

    assert wd.check(db, send, NOON) is None and db.flag(wd.FLAG) is None      # failed: not marked as told
    fail[0] = False
    assert "has not run for 6 min" in wd.check(db, send, NOON + timedelta(minutes=1)) and len(sent) == 1


def test_outside_alert_hours_a_stale_heartbeat_is_not_a_text(tmp_path):
    db, sent = OpsStore(tmp_path / "ops.db"), []
    night = datetime(2026, 11, 23, 23, 30, tzinfo=ET).astimezone(timezone.utc)   # a deploy at night
    beat(db, night - timedelta(minutes=10))
    assert wd.check(db, sent.append, night) is None
    sunday = datetime(2026, 11, 22, 12, 0, tzinfo=ET).astimezone(timezone.utc)
    beat(db, sunday - timedelta(hours=3))
    assert wd.check(db, sent.append, sunday) is None and sent == []


def test_the_watchdog_is_capped_and_never_raises_into_the_control_job(tmp_path, monkeypatch):
    db, sent = OpsStore(tmp_path / "ops.db"), []
    beat(db, NOON - timedelta(hours=1))
    for k in range(wd.CAP + 3):                              # stuck all day: the cap holds
        wd.check(db, sent.append, NOON + k * (wd.AGAIN + timedelta(minutes=1)))
    assert sum("has not run" in t for t in sent) == wd.CAP
    monkeypatch.setattr(db, "flag", lambda *a, **k: 1 / 0)
    assert wd.check(db, sent.append, NOON) is None           # a broken database: logged, the job goes on


def test_the_control_job_runs_the_watchdog_every_minute():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "scripts" / "sms_control.py").read_text()
    assert "watchdog.check(db, sms.send, now)" in src.split("def cmd_poll")[1][:900]
