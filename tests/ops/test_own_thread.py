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


# ---- the observed timing of stop checks, signs of life, an event loop that stands still (2026-11-23) --------------

def test_the_intervals_between_stop_checks_are_measured_not_assumed(tmp_path):
    clock = [0.0]
    pace = ot.Pace(tmp_path, "short book", 10, clock=lambda: clock[0])
    day, noon = datetime.now(timezone.utc).date().isoformat(), lambda t: True
    pace.start()
    before = 0.0                                             # how long the check before took
    for gap, work in [(10.1, 0.2)] * 40 + [(12.0, 1.5), (14.0, 3.0), (10.2, 0.1), (21.0, 9.5)] + [(10.0, 0.1)] * 30:
        clock[0] += gap - before                             # this check starts ``gap`` after the one before it started
        pace.start()
        clock[0] += work
        pace.finish()                                        # ... and takes ``work``
        before = work
    pace.flush()
    rows = [r for r in ot.gaps(tmp_path, day) if r.get("kind") == "summary"]
    assert sum(r["n"] for r in rows) == 74 and len(rows) == 2                  # one written at 10 minutes, one at the end
    total = [sum(r["counts"][i] for r in rows) for i in range(4)]
    assert total == [71, 2, 1, 0]                                              # within 11 s / 15 s / 25 s / later
    assert max(r["max_gap"] for r in rows) == 21.0 and max(r["max_work"] for r in rows) == 9.5
    assert ot.clause(tmp_path, day, noon) == (
        "stop checks: measured in alert hours: short book 74 checks, 95.9% within 11 s, longest gap 21 s, longest "
        "single check 10 s; none late, no restart in alert hours")
    assert ot.clause(tmp_path, day, lambda t: False) == "stop checks: none late, no restart in alert hours"   # not in hours


def test_a_loops_sign_of_life_is_a_file_not_a_database_write(tmp_path):
    assert ot.beat_age(tmp_path, "long book") is None                           # never beat: unknown, not silent
    ot.beat(tmp_path, "long book")
    assert 0 <= ot.beat_age(tmp_path, "long book") < 2 and (tmp_path / "beat.long_book").exists()
    assert ot.beat_age(tmp_path, "long book", now=time.time() + 400) > 399
    ot.Pace(tmp_path, "short book", 10).start()
    assert ot.beat_age(tmp_path, "short book") < 2                              # every stop check beats
    ot.beat(tmp_path / "no" / "folder", "x")                                    # cannot be written: logged, not raised


def test_an_event_loop_that_stands_still_is_written_down_whatever_held_it(tmp_path):
    async def run():
        watch = asyncio.create_task(ot.watch_loop(tmp_path, every=0.02, late=0.2))
        await asyncio.sleep(0.1)
        time.sleep(0.5)                                      # blocking work ON the loop, as a slow database write was
        await asyncio.sleep(0.2)
        watch.cancel()

    asyncio.run(run())
    day = datetime.now(timezone.utc).date().isoformat()
    (g,) = ot.gaps(tmp_path, day)
    assert g["loop"] == ot.EVENT_LOOP and g["gap"] >= 0.5
    assert ot.clause(tmp_path, day).startswith("⚠️ stop checks: 1 started late, longest gap ") and "(event loop" in \
        ot.clause(tmp_path, day)
