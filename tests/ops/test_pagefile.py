"""Putting a built page in place (sgt.ops.pagefile): whole, from a file of its own, never over a newer page."""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sgt.ops import pagefile

T0 = datetime(2026, 11, 24, 21, 14, 35, tzinfo=timezone.utc)


def names(d: Path) -> list[str]:
    return sorted(p.name for p in d.iterdir())


def test_two_builds_at_once_both_succeed_and_leave_one_whole_page(tmp_path):
    """2026-11-24 16:14 ET: the container's start-up build and the deploy script's build ran 3 s apart and shared one
    temporary file; the first renamed it away and the second died (FileNotFoundError, "jobs FAILED: charts")."""
    out = tmp_path / "charts" / "fade_tape.html"
    go, res, errs = threading.Event(), [], []

    def run(html, at):
        go.wait(5)
        try:
            res.append(pagefile.publish(out, html, at))
        except Exception as e:  # noqa: BLE001 - the failure is the finding
            errs.append(e)

    builds = [threading.Thread(target=run, args=(chr(65 + k) * 200_000, T0 + timedelta(seconds=k))) for k in range(8)]
    for b in builds:
        b.start()
    go.set()
    for b in builds:
        b.join(10)
    assert errs == [] and len(res) == 8 and res.count(True) >= 1
    page = out.read_text()
    assert page == "H" * 200_000                                     # whole, and the build with the newest data
    assert names(out.parent) == ["fade_tape.html", "fade_tape.html.built", "fade_tape.html.lock"]
    assert oct(out.stat().st_mode & 0o777) == "0o644"


def test_an_older_build_that_finishes_later_does_not_replace_the_newer_page(tmp_path):
    out = tmp_path / "fade_tape.html"
    assert pagefile.publish(out, "NEW", T0 + timedelta(seconds=3)) is True
    assert pagefile.publish(out, "OLD", T0) is False                 # started first, finished last: dropped
    assert out.read_text() == "NEW" and names(tmp_path) == ["fade_tape.html", "fade_tape.html.built", "fade_tape.html.lock"]
    assert pagefile.publish(out, "SAME", T0 + timedelta(seconds=3)) is True     # the same data again: the later write
    assert pagefile.publish(out, "NEWER", T0 + timedelta(minutes=10)) is True and out.read_text() == "NEWER"


def test_the_order_holds_when_the_older_build_is_inside_its_publish_as_the_newer_one_arrives(tmp_path, monkeypatch):
    out = tmp_path / "fade_tape.html"
    inside, go, real = threading.Event(), threading.Event(), Path.replace

    def replace(self, target):
        if str(target) == str(out) and not inside.is_set():         # the first build stands just before its rename
            inside.set()
            go.wait(5)
        return real(self, target)

    monkeypatch.setattr(Path, "replace", replace)
    res = {}
    old = threading.Thread(target=lambda: res.update(old=pagefile.publish(out, "OLD", T0)))
    old.start()
    assert inside.wait(5)
    new = threading.Thread(target=lambda: res.update(new=pagefile.publish(out, "NEW", T0 + timedelta(seconds=3))))
    new.start()
    time.sleep(0.2)
    assert "new" not in res                                          # it waits for the older build's step to finish
    go.set()
    old.join(5)
    new.join(5)
    assert res == {"old": True, "new": True} and out.read_text() == "NEW"


def test_a_file_left_by_a_killed_build_is_removed_after_an_hour_and_a_bad_stamp_blocks_nothing(tmp_path):
    out = tmp_path / "fade_tape.html"
    killed, fresh = tmp_path / "fade_tape.abc.tmp", tmp_path / "fade_tape.def.tmp"
    killed.write_text("x")
    fresh.write_text("y")                                            # another build is writing this one right now
    os.utime(killed, (time.time() - 2 * pagefile.STALE_TMP_S,) * 2)
    (tmp_path / "fade_tape.html.built").write_text("not a time")
    assert pagefile.publish(out, "C", T0) is True and out.read_text() == "C"
    assert names(tmp_path) == ["fade_tape.def.tmp", "fade_tape.html", "fade_tape.html.built", "fade_tape.html.lock"]
    with pytest.raises(ValueError):
        pagefile.publish(out, "D", datetime(2026, 11, 24, 21, 0))    # a build must say when, with a zone
    assert out.read_text() == "C"
