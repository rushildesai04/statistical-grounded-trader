"""A loop's own worker thread and the record of late stop checks (operational risk review, 2026-11-23)."""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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
    # 2026-11-25 (BNAI): the long book's figure is how often it LOOKS at closed free-feed candles, never how soon it
    # sees a long's stop. The line says so for the long book and only for it.
    lp = ot.Pace(tmp_path, "long book", 15, clock=lambda: clock[0])
    lp.start()
    for _ in range(5):
        clock[0] += 15.0
        lp.start()
        lp.finish()
    lp.flush()
    text = ot.clause(tmp_path, day, noon)
    assert ("long book 5 checks, 100.0% within 16 s, longest gap 15 s, longest single check 0 s (how often it LOOKS, "
            "at closed free-feed candles and at the free feed's last trade; that feed shows a long's stop later than the "
            "tape, or not at all: not stop protection); short book 74 checks") in text
    assert text.count("not stop protection") == 1


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


def row(at, loop="long book", **kw):
    return {"at": at, "loop": loop, **kw}


def test_the_record_is_one_file_a_utc_day_and_the_old_single_file_is_read_and_never_written(tmp_path):
    """2026-11-27: the one file stopped taking rows for good at 1 MB (full about 2026-12-14). Now a file per day."""
    old = tmp_path / ot.GAPS_NAME
    old.write_text(json.dumps(row("2026-11-17T15:00:00+00:00", gap=40.0, every=15.0)) + "\n")
    before = old.read_bytes()
    ot._write(tmp_path, row("2026-11-17T23:59:59.999999+00:00", gap=41.0, every=15.0))               # the last instant of a day
    ot._write(tmp_path, row("2026-11-18T00:00:00+00:00", gap=42.0, every=15.0))                      # the first of the next
    assert old.read_bytes() == before
    assert sorted(f.name for f in tmp_path.iterdir()) == ["loop_gaps.2026-11-17.jsonl", "loop_gaps.2026-11-18.jsonl", "loop_gaps.jsonl"]
    assert [g["gap"] for g in ot.gaps(tmp_path, "2026-11-17")] == [40.0, 41.0]                       # old file first: time order
    assert [g["gap"] for g in ot.gaps(tmp_path, "2026-11-18")] == [42.0] and ot.gaps(tmp_path, "2026-11-19") == []
    assert ot.files(tmp_path, ["2026-11-18", "2026-11-17", "2026-11-17"]) == [old, ot.part(tmp_path, "2026-11-17"), ot.part(tmp_path, "2026-11-18")]
    ot._write(tmp_path, {"loop": "long book", "gap": 43.0})                                          # a row without a stamp: today's file
    assert ot.part(tmp_path, datetime.now(timezone.utc).date().isoformat()).exists()
    ot._write(tmp_path, row("../../etc/x", gap=1.0))                                                 # a stamp is never a path
    assert all(f.parent == tmp_path for f in tmp_path.rglob("*")) and len(list(tmp_path.iterdir())) == 4


def test_a_full_day_stops_late_rows_first_keeps_the_interval_rows_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(ot, "GAPS_MAX_BYTES", 400)
    day, p = "2026-11-30", ot.part(tmp_path, "2026-11-30")
    late = row(f"{day}T15:00:00+00:00", gap=40.0, every=15.0)
    while p.stat().st_size <= 400 if p.exists() else True:
        ot._write(tmp_path, late)
    full = p.stat().st_size
    assert ot.capped(tmp_path, day) and not ot.capped(tmp_path, "2026-12-01")
    ot._write(tmp_path, late)
    assert p.stat().st_size == full                                                                  # a late row: not written
    ot._write(tmp_path, row(f"{day}T15:10:00+00:00", kind="summary", n=40, counts=[40, 0, 0, 0], max_gap=15.0, max_work=0.1, every=15.0))
    assert p.stat().st_size > full                                                                   # an interval row: still written
    while p.stat().st_size <= 800:
        ot._write(tmp_path, row(f"{day}T15:10:00+00:00", kind="summary", n=40, counts=[40, 0, 0, 0], max_gap=15.0, max_work=0.1, every=15.0))
    top = p.stat().st_size
    ot._write(tmp_path, row(f"{day}T15:20:00+00:00", kind="summary", n=40, counts=[40, 0, 0, 0], max_gap=15.0, max_work=0.1, every=15.0))
    assert p.stat().st_size == top                                                                   # the day's hard stop
    assert "the record of stop checks is full for this day (later late checks are not in it)" in ot.clause(tmp_path, day)
    ot._write(tmp_path, row("2026-12-01T15:00:00+00:00", gap=40.0, every=15.0))                      # the next day starts clean
    assert len(ot.gaps(tmp_path, "2026-12-01")) == 1 and "is full" not in ot.clause(tmp_path, "2026-12-01")


def test_a_line_torn_by_a_crash_or_a_full_disk_costs_one_line_and_never_the_row_after_it(tmp_path, monkeypatch):
    day, p = "2026-11-30", ot.part(tmp_path, "2026-11-30")
    ot._write(tmp_path, row(f"{day}T15:00:00+00:00", gap=40.0, every=15.0))
    with p.open("a") as f:
        f.write('{"at": "2026-11-30T15:00:15+00:00", "loop": "long bo')                              # the process died mid-line
    monkeypatch.setattr(ot, "_tail_seen", None)                                                      # a restart: a new process
    ot._write(tmp_path, row(f"{day}T15:01:00+00:00", gap=41.0, every=15.0))
    ot._write(tmp_path, row(f"{day}T15:02:00+00:00", gap=42.0, every=15.0))
    assert [g["gap"] for g in ot.gaps(tmp_path, day)] == [40.0, 41.0, 42.0]                          # the torn line is skipped
    assert len(p.read_text().splitlines()) == 4 and p.read_text().endswith("\n")
    real = type(p).open

    def no_room(self, mode="r", *a, **k):                                                            # the volume is full
        if "a" in mode:
            raise OSError(28, "No space left on device")
        return real(self, mode, *a, **k)
    monkeypatch.setattr(type(p), "open", no_room)
    pace, clock = ot.Pace(tmp_path, "long book", 15, clock=lambda: clock[0]), [0.0]
    pace.start()
    clock[0] = 99.0
    assert pace.start() == 99.0                                                                      # a late pass: not raised
    pace.flush()                                                                                     # nor the interval row
    ot._write(tmp_path, row(f"{day}T15:02:30+00:00", gap=99.0, every=15.0))
    assert ot._tail_seen is None                                                                     # look at the tail again next time
    monkeypatch.setattr(type(p), "open", real)
    with p.open("a") as f:
        f.write('{"torn by the full disk')
    ot._write(tmp_path, row(f"{day}T15:03:00+00:00", gap=43.0, every=15.0))
    assert [g["gap"] for g in ot.gaps(tmp_path, day)] == [40.0, 41.0, 42.0, 43.0]


def test_a_write_does_not_read_the_file_or_list_the_folder(tmp_path, monkeypatch):
    """Lightweight and never waiting: one stat and one append; the last byte is looked at once per process and day."""
    day, p = "2026-11-30", ot.part(tmp_path, "2026-11-30")
    ot._write(tmp_path, row(f"{day}T15:00:00+00:00", gap=40.0, every=15.0))
    src = Path(ot.__file__).read_text()
    opened, real = [], type(p).open

    def spy(self, mode="r", *a, **k):
        opened.append(mode)
        return real(self, mode, *a, **k)
    monkeypatch.setattr(type(p), "open", spy)
    monkeypatch.setattr(type(p), "glob", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(type(p), "iterdir", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(type(p), "read_text", lambda *a, **k: 1 / 0)
    for k in range(5):
        ot._write(tmp_path, row(f"{day}T15:0{k + 1}:00+00:00", gap=40.0, every=15.0))
    assert opened == ["a"] * 5 and len(real(p).read().splitlines()) == 6
    assert "fsync" not in src and "flock" not in src and src.count("= prune(") == 1                  # only the nightly line prunes


def test_day_files_older_than_the_retention_are_removed_and_nothing_else_is(tmp_path):
    today = date(2027, 2, 1)
    names = {"loop_gaps.2026-12-02.jsonl": False,          # 61 days old: removed
             "loop_gaps.2026-12-03.jsonl": True,           # exactly 60: kept
             "loop_gaps.2027-02-01.jsonl": True, "loop_gaps.2027-02-02.jsonl": True,   # today's and a later day's
             "loop_gaps.jsonl": True,                      # the old single file: never removed
             "loop_gaps.2026-13-45.jsonl": True, "loop_gaps.backup.jsonl": True, "loop_gaps.2026-01-01.jsonl.bak": True,
             "ops.db": True, "beat.long_book": True}
    for n in names:
        (tmp_path / n).write_text("x\n")
    (tmp_path / "loop_gaps.2026-01-01.jsonl").mkdir()                                                # not a file: left, not raised
    assert ot.GAPS_KEEP_DAYS == 60 and ot.prune(tmp_path, today) == ["loop_gaps.2026-12-02.jsonl"]
    assert {n for n in names if (tmp_path / n).exists()} == {n for n, kept in names.items() if kept}
    assert ot.prune(tmp_path, today) == [] and ot.prune(tmp_path / "missing", today) == []
    (tmp_path / "loop_gaps.2020-01-01.jsonl").write_text("x\n")
    text = ot.clause(tmp_path, "2026-11-23")                                                         # the nightly line prunes and says so
    assert text == "stop checks: none late, no restart in alert hours; 1 day files of the record older than 60 days removed"
    assert not (tmp_path / "loop_gaps.2020-01-01.jsonl").exists() and (tmp_path / "loop_gaps.2026-12-03.jsonl").exists()


def test_a_day_whose_session_has_no_recorder_check_is_never_pruned_and_nothing_outside_the_record_is_touched(tmp_path):
    """Owner 2026-11-27 (night): pruning cannot remove unevaluated session data, research evidence or the records
    kept for a breach."""
    ops, shadow, today = tmp_path / "reports" / "ops", tmp_path / "reports" / "feed_shadow", date(2027, 3, 1)
    ops.mkdir(parents=True)
    (shadow / "evidence").mkdir(parents=True)
    days = {"2026-11-30": "a session, checked", "2026-12-01": "a session, NO line", "2026-12-05": "a Saturday",
            "2026-12-25": "the exchange is closed", "2027-02-26": "inside the retention"}
    for d in days:
        ot.part(ops, d).write_text("x\n")
    (shadow / "daily.jsonl").write_text(json.dumps({"session": "2026-11-30", "report": "feed recorder 2026-11-30: timing inside"})
                                        + "\nnot json\n" + json.dumps({"session": "2026-12-02"}) + "\n")
    others = [shadow / "OFF", shadow / "shadow.db", shadow / "evidence" / "2026-11-30.json", shadow / "loop_gaps.2020-01-01.jsonl",
              ops / "ops.db", ops / "loop_gaps.jsonl", tmp_path / "reports" / "alert_fade" / "trades.csv", tmp_path / "data" / "fast" / "bars.parquet"]
    for f in others:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("keep\n")
    held: list[str] = []
    assert ot.prune(ops, today, kept=held) == ["loop_gaps.2026-11-30.jsonl", "loop_gaps.2026-12-05.jsonl", "loop_gaps.2026-12-25.jsonl"]
    assert held == ["loop_gaps.2026-12-01.jsonl"] and ot.part(ops, "2026-12-01").exists() and ot.part(ops, "2027-02-26").exists()
    assert all(f.read_text() == "keep\n" for f in others) and (shadow / "daily.jsonl").read_text().count("\n") == 3
    (shadow / "daily.jsonl").unlink()                                                                # the check is not in use: nothing waits
    assert ot.prune(ops, today) == ["loop_gaps.2026-12-01.jsonl"]
    ot.part(ops, "2026-12-08").write_text("x\n")
    (shadow / "daily.jsonl").mkdir()                                                                 # cannot be read: undecided, keep
    held = []
    assert ot.prune(ops, today, kept=held) == [] and held == ["loop_gaps.2026-12-08.jsonl"]
    (shadow / "daily.jsonl").rmdir()
    (shadow / "daily.jsonl").write_text("")                                                          # in use, no line at all
    ot.part(ops, "2020-01-02").write_text("x\n")
    text = ot.clause(ops, "2026-11-23")
    assert text.endswith("; 1 older day files kept: their sessions have no recorder check yet") and ot.part(ops, "2020-01-02").exists()   # 2026-12-08 is not old yet
