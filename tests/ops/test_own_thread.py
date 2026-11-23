"""A loop's own worker thread and the record of late stop checks (operational risk review, 2026-11-23)."""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

from sgt.ops import own_thread as ot
from sgt.ops.store import OpsStore


def test_a_pass_on_its_own_thread_does_not_wait_for_the_shared_workers():
    """Every worker of the shared pool is busy (research loops waiting on slow network calls). A pass handed to the
    shared pool waits behind them; the same pass on its own thread runs at once."""
    async def run():
        loop, hold = asyncio.get_running_loop(), threading.Event()
        workers = loop._default_executor._max_workers if loop._default_executor else None
        busy = [asyncio.create_task(asyncio.to_thread(hold.wait, 5)) for _ in range(40)]     # far more than any pool has
        await asyncio.sleep(0.1)
        own, t0 = ot.OwnThread("short-book"), time.monotonic()
        shared = asyncio.create_task(asyncio.to_thread(lambda: "shared"))
        assert await own.run(lambda: threading.current_thread().name) == "short-book_0"
        mine = time.monotonic() - t0
        await asyncio.sleep(0.3)
        starved = not shared.done()
        hold.set()
        await asyncio.gather(shared, *busy)
        return mine, starved, workers

    mine, starved, _ = asyncio.run(run())
    assert mine < 0.5 and starved


def test_a_pass_that_starts_late_is_written_down_and_one_on_time_is_not(tmp_path):
    clock = [100.0]
    pace = ot.Pace(tmp_path, "short book", 10, clock=lambda: clock[0])
    day = datetime.now(timezone.utc).date().isoformat()
    assert pace.start() is None                              # the first pass has nothing before it
    for step in (10.2, 14.0, 25.0):                          # on time, a slow pass, exactly the limit: not late
        clock[0] += step
        assert pace.start() is None
    assert ot.clause(tmp_path, day) == "stop checks: none late, no restart in alert hours"
    clock[0] += 48.0                                         # four checks did not happen
    assert pace.start() == 48.0
    clock[0] += 10.0
    assert pace.start() is None
    (g,) = ot.gaps(tmp_path, day)
    assert (g["loop"], g["gap"], g["every"]) == ("short book", 48.0, 10.0)
    assert ot.clause(tmp_path, day) == "⚠️ stop checks: 1 started late, longest gap 48 s (short book, due every 10 s)"
    assert ot.late_after(10) == 25 and ot.late_after(15) == 35


def test_a_restart_inside_alert_hours_is_a_warning_and_a_deploy_at_night_is_not(tmp_path):
    night, noon = datetime(2026, 11, 23, 4, 59, tzinfo=timezone.utc), datetime(2026, 11, 23, 16, 30, tzinfo=timezone.utc)
    in_hours = lambda t: 14 <= t.hour < 21  # noqa: E731
    assert ot.note_restart(tmp_path, (night - timedelta(seconds=20)).isoformat(), night) is None      # too short to note
    assert ot.note_restart(tmp_path, (night - timedelta(seconds=75)).isoformat(), night) == 75.0      # a deploy
    assert ot.note_restart(tmp_path, None, night) is None and ot.note_restart(tmp_path, "garbage", night) is None
    assert ot.clause(tmp_path, "2026-11-23", in_hours) == "stop checks: none late, no restart in alert hours"
    assert ot.note_restart(tmp_path, (noon - timedelta(seconds=140)).isoformat(), noon) == 140.0      # a crash at 11:30 ET
    assert ot.clause(tmp_path, "2026-11-23", in_hours) == (
        "⚠️ stop checks: listener restarted 1x in alert hours, longest silence 140 s (at 16:30 UTC)")


def test_the_record_never_breaks_the_pass_it_measures(tmp_path, monkeypatch):
    pace, clock = ot.Pace(tmp_path / "missing" / "folder", "short book", 10, clock=lambda: clock[0]), [0.0]
    pace.start()
    clock[0] = 99.0
    assert pace.start() == 99.0                              # the folder does not exist: logged, not raised
    monkeypatch.setattr(ot, "gaps", lambda *a: 1 / 0)
    assert ot.clause(tmp_path, "2026-11-23") == "⚠️ stop checks: check failed (ZeroDivisionError)"


def test_reading_a_flag_does_not_wait_for_a_writer(tmp_path):
    """Every switch the books consult is a flag read. It used to take the database's write lock (as every block did),
    so the stop check's ``paused?`` waited behind any writer."""
    db = OpsStore(tmp_path / "ops.db")
    db.set_flag("paused", "0")
    got, release = [], threading.Event()

    def writer():
        with db.tx() as c:
            c.execute("UPDATE flags SET value='1' WHERE key='paused'")
            got.append("locked")
            release.wait(5)

    t = threading.Thread(target=writer)
    t.start()
    while not got:
        time.sleep(0.01)
    t0 = time.monotonic()
    assert db.flag("paused") == "0" and not db.paused()      # the last committed value, at once
    took = time.monotonic() - t0
    release.set()
    t.join()
    assert took < 0.5 and db.flag("paused") == "1" and db.flag("absent", "d") == "d"


def test_a_slow_write_names_the_code_that_asked_for_it_not_the_store_helper(tmp_path, monkeypatch):
    from sgt.ops import store

    monkeypatch.setattr(store, "SLOW_S", 0.0)
    db = OpsStore(tmp_path / "ops.db")

    def heartbeat():
        db.set_flag("fade_heartbeat", "x")

    heartbeat()
    who = db.slow(datetime.now(timezone.utc).date().isoformat())[-1]["who"]
    assert who.startswith("test_own_thread.py:heartbeat:") and who.endswith(" via set_flag")
