from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import preflight as pf  # noqa: E402
from sgt.ops.store import OpsStore  # noqa: E402

NOW = datetime(2026, 11, 9, 14, 20, tzinfo=timezone.utc)


def test_all_good_is_silent(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(100, 50, 50))     # not the test machine's real disk
    db = OpsStore(tmp_path / "ops.db")
    db.set_flag("fade_heartbeat", (NOW - timedelta(seconds=20)).isoformat())
    assert pf.run(db, NOW, quote_time=lambda: NOW - timedelta(minutes=1), disk=tmp_path) == []


def test_each_failure_is_one_plain_line(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    assert "never reported" in pf.check_heartbeat(db, NOW)
    db.set_flag("fade_heartbeat", (NOW - timedelta(hours=2)).isoformat())
    assert "DOWN (last heartbeat 120 min ago)" in pf.check_heartbeat(db, NOW)

    def boom():
        raise TimeoutError

    assert "not responding (TimeoutError)" in pf.check_data(boom, NOW)
    assert "stale" in pf.check_data(lambda: NOW - timedelta(days=4), NOW)     # older than the last regular session
    msg = pf.message(["a", "b"], NOW)
    assert msg.startswith("[SGT] Pre-market check found a problem") and "\n- a\n- b\n" in msg


def test_full_disk_is_reported(monkeypatch, tmp_path):
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(100, 95, 5))
    assert "95% full" in pf.check_disk(tmp_path)


def test_a_switched_off_book_is_said_before_the_open(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    assert pf.switches(db) == []                                   # flags absent = everything on = silent
    db.set_flag("fade_off", "0")
    db.set_flag("long_off", "0")
    assert pf.switches(db) == []
    db.set_flag("fade_off", "1")
    (line,) = pf.switches(db)
    assert "alert shorts" in line and "OFF" in line and "longs" not in line
    db.set_flag("long_off", "1")
    db.set_flag("paused", "1")
    lines = pf.switches(db)
    assert len(lines) == 3 and "PAUSED" in lines[0] and "alert longs are measure-only" in lines[2]


def test_the_switch_text_is_not_worded_as_a_fault(tmp_path):
    off = ["alert shorts (fade) are OFF: no short trades and no short texts today"]
    only = pf.message([], NOW, off)
    assert only.startswith("[SGT] Before the open: Switched off today:\n- alert shorts")
    assert "problem" not in only and "The system is running" in only
    both = pf.message(["the fade listener is DOWN"], NOW, off)
    assert both.startswith("[SGT] Pre-market check found a problem") and both.count("[SGT]") == 1
    assert "\nSwitched off today:\n- alert shorts" in both
    assert pf.message(["a"], NOW) == pf.message(["a"], NOW, [])   # no switch off: the old text, unchanged


def test_main_texts_the_switches_even_when_every_check_passes(tmp_path, monkeypatch, capsys):
    db = OpsStore(tmp_path / "ops.db")
    db.set_flag("long_off", "1")
    sent = []

    class FakeSms:
        def send(self, text):
            sent.append(text)

        def close(self):
            pass

    monkeypatch.setattr(pf, "OpsStore", lambda: db)
    monkeypatch.setattr(pf, "Sms", FakeSms)
    monkeypatch.setattr(pf, "run", lambda d, now: [])
    from sgt.intraday import calendar

    class Open:
        def is_session(self, d):
            return True

    monkeypatch.setattr(calendar, "default_calendar", lambda: Open())
    assert pf.main() == 0
    assert len(sent) == 1 and "alert longs are measure-only" in sent[0] and "problem" not in sent[0]
    db.set_flag("long_off", "0")
    assert pf.main() == 0 and len(sent) == 1                       # all on: silent, as before
    assert "all checks passed" in capsys.readouterr().out
    monkeypatch.setattr(pf, "switches", lambda d: (_ for _ in ()).throw(RuntimeError("x")))
    assert pf.main() == 0 and len(sent) == 2 and "could not be read (RuntimeError)" in sent[1]


def _utc(day, hm):
    from zoneinfo import ZoneInfo

    h, m = map(int, hm.split(":"))
    y, mo, d = map(int, day.split("-"))
    return datetime(y, mo, d, h, m, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)


def test_before_the_open_the_last_regular_print_is_fresh_enough():
    # 2026-11-17: the 09:20 run texted "market data looks stale" on 6 of 7 mornings; the free feed prints little
    # before the open. Tuesday 09:20: Monday's closing print is the newest print that must exist.
    now = _utc("2026-11-17", "09:20")
    assert pf.oldest_ok(now) == _utc("2026-11-16", "15:45")
    assert pf.check_data(lambda: _utc("2026-11-16", "15:59"), now) is None
    assert pf.check_data(lambda: _utc("2026-11-17", "09:01"), now) is None           # a pre-market print: fine too
    assert "stale" in pf.check_data(lambda: _utc("2026-11-16", "15:30"), now)         # before that close's last 15 min
    assert "stale" in pf.check_data(lambda: _utc("2026-11-13", "15:59"), now)         # a whole session old
    assert "stale" in pf.check_data(lambda: None, now)
    # Monday morning looks back over the weekend to Friday's close
    assert pf.oldest_ok(_utc("2026-11-16", "09:20")) == _utc("2026-11-13", "15:45")
    assert pf.check_data(lambda: _utc("2026-11-13", "15:59"), _utc("2026-11-16", "09:20")) is None


def test_in_regular_hours_the_rule_is_still_fifteen_minutes():
    now = _utc("2026-11-17", "10:30")
    assert pf.oldest_ok(now) == now - timedelta(minutes=15)
    assert pf.check_data(lambda: now - timedelta(minutes=14), now) is None
    assert "stale" in pf.check_data(lambda: now - timedelta(minutes=16), now)
    assert "stale" in pf.check_data(lambda: _utc("2026-11-16", "15:59"), now)         # yesterday's close is stale now
    # after the close the same day's closing print is the reference
    assert pf.oldest_ok(_utc("2026-11-17", "18:00")) == _utc("2026-11-17", "15:45")


def test_a_broken_calendar_falls_back_to_the_strict_rule():
    class Broken:
        def is_session(self, d):
            raise RuntimeError("no calendar")

    now = _utc("2026-11-17", "09:20")
    assert "stale" in pf.check_data(lambda: _utc("2026-11-16", "15:59"), now, cal=Broken())
    assert pf.check_data(lambda: now - timedelta(minutes=5), now, cal=Broken()) is None

    class Closed:
        def is_session(self, d):
            return False

    assert pf.oldest_ok(now, Closed()) == now - timedelta(minutes=15)                 # no session found: strict


def test_run_hands_its_calendar_to_the_data_check(tmp_path, monkeypatch):
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: usage(100, 50, 50))

    class Closed:
        def is_session(self, d):
            return False

    now = _utc("2026-11-17", "09:20")
    db = OpsStore(tmp_path / "ops.db")
    db.set_flag("fade_heartbeat", (now - timedelta(seconds=20)).isoformat())
    last_close = lambda: _utc("2026-11-16", "15:59")  # noqa: E731
    assert pf.run(db, now, quote_time=last_close, disk=tmp_path) == []                 # real calendar: fine
    (line,) = pf.run(db, now, quote_time=last_close, disk=tmp_path, cal=Closed())      # no session known: strict
    assert "stale" in line


def test_the_alert_intake_is_checked_before_the_open_from_the_runners_own_schedule(tmp_path):
    """2026-11-23: the pre-open look at the alert intake must not depend on a chat session being open. It is part of
    the 09:20 ET check the runner runs itself: a channel not read for 15 minutes, an unfinished catch-up, or a
    stretch of the channel passed unhandled since yesterday's close is one plain line each."""
    import os

    from sgt.ops import alert_log, own_thread

    db = OpsStore(tmp_path / "ops.db")
    assert pf.check_alerts(db, NOW) == []                    # nothing known yet: nothing claimed
    own_thread.beat(tmp_path, "alerts")
    t = NOW.timestamp() - 120
    os.utime(tmp_path / "beat.alerts", (t, t))
    db.set_flag("alert_catchup", '{"ok": true}')
    assert pf.check_alerts(db, NOW) == []
    t = NOW.timestamp() - 20 * 60
    os.utime(tmp_path / "beat.alerts", (t, t))
    db.set_flag("alert_catchup", '{"ok": false, "stuck": 27300}')
    alert_log.skip(db, "-1", 27290, 27295, "the message could not be read as alerts", n=1)
    got = pf.check_alerts(db, datetime.now(timezone.utc))    # the gap row is stamped with the wall clock
    assert any("catch-up is not complete (stopped at message 27300)" in g for g in got)
    assert any("1 stretch of the alert channel were passed unhandled" in g for g in got)
    assert pf.check_alerts(db, NOW)[0] == "the alert channel has not been read for 20 min: alerts will be missed"
    wall = datetime.now(timezone.utc)                        # the window is "since the previous pre-open check":
    assert not any("passed unhandled" in g for g in pf.check_alerts(db, wall, since=wall + timedelta(seconds=1)))
    assert any("passed unhandled" in g for g in pf.check_alerts(db, wall, since=wall - timedelta(days=3)))   # a weekend
    src = (Path(__file__).resolve().parents[2] / "deploy" / "fly" / "crontab").read_text()
    assert "job preflight" in src                            # scheduled on the runner, every weekday morning
