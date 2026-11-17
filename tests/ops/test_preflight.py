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
    assert "stale" in pf.check_data(lambda: NOW - timedelta(hours=1), NOW)
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
    assert len(lines) == 3 and "PAUSED" in lines[0] and "alert longs are OFF" in lines[2]


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
    assert len(sent) == 1 and "alert longs are OFF" in sent[0] and "problem" not in sent[0]
    db.set_flag("long_off", "0")
    assert pf.main() == 0 and len(sent) == 1                       # all on: silent, as before
    assert "all checks passed" in capsys.readouterr().out
    monkeypatch.setattr(pf, "switches", lambda d: (_ for _ in ()).throw(RuntimeError("x")))
    assert pf.main() == 0 and len(sent) == 2 and "could not be read (RuntimeError)" in sent[1]
