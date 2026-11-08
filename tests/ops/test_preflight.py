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
