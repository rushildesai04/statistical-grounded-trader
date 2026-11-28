"""Feed shadow, Stage 0 (owner 2026-11-27): a passive recorder beside the long book's candle reader. The tests are
about the two things that matter: the book can never be affected, and a missing live candle is classified as what
it was (nothing traded / traded elsewhere only / never delivered)."""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from sgt.fast import feed_shadow as fs

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import feed_shadow as job  # noqa: E402

DAY = "2026-11-09"
OPEN = pd.Timestamp("2026-11-09 14:30", tz="UTC")                      # 09:30 ET
CLOSE = pd.Timestamp("2026-11-09 21:00", tz="UTC")


def at(minute: int, second: int = 0) -> pd.Timestamp:
    return OPEN + pd.Timedelta(minutes=minute, seconds=second)


def frame(minutes, px=10.0, bump=None):
    """A reader's frame holding candles at these minute offsets (bump: {minute: high} to move one candle)."""
    rows = [(at(m), px, (bump or {}).get(m, px + 0.1), px - 0.1, px, 100.0) for m in minutes]
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])


def rows(db, sql, *a):
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(sql, a)]
    finally:
        c.close()


def run(tmp_path, looks):
    """A real recorder fed ``looks`` = [(time, symbol, frame)] with its clock set to each look's time."""
    clock = {"at": looks[0][0].to_pydatetime()}
    rec = fs.Recorder(tmp_path / "shadow.db", clock=lambda: clock["at"]).start()
    for when, sym, f in looks:
        clock["at"] = when.to_pydatetime()
        rec.offer(sym, when.to_pydatetime(), f, 12.5)
    rec.stop()
    return rec


# ---- the book is never affected ----
def test_off_by_default_the_reader_is_handed_back_untouched(monkeypatch, tmp_path):
    monkeypatch.delenv(fs.ENV, raising=False)
    assert fs.start(tmp_path / "x.db") is None
    reader = lambda sym, now: frame([0])                               # noqa: E731
    assert fs.tap(None, reader) is reader and not (tmp_path / "x.db").exists()
    monkeypatch.setenv(fs.ENV, "0")
    assert fs.start(tmp_path / "x.db") is None


def test_on_the_same_frame_object_comes_back_and_the_look_is_recorded(monkeypatch, tmp_path):
    monkeypatch.setenv(fs.ENV, "1")
    rec = fs.start(tmp_path / "shadow.db")
    f = frame([0, 1, 2])
    tapped = fs.tap(rec, lambda sym, now: f)
    assert tapped("ABCD", at(3, 5).to_pydatetime()) is f               # the very object, unchanged
    rec.stop()
    (look,) = rows(tmp_path / "shadow.db", "SELECT * FROM looks")
    assert (look["symbol"], look["n_bars"], look["session"]) == ("ABCD", 3, rec.clock().astimezone(fs.ET).date().isoformat())
    assert len(rows(tmp_path / "shadow.db", "SELECT * FROM bars")) == 3 and list(f.columns) == ["ts", "open", "high", "low", "close", "volume"]


def test_a_recorder_that_fails_never_reaches_the_book(tmp_path):
    rec = fs.Recorder(tmp_path / "shadow.db")                          # not started: nothing drains the queue

    def boom(*a, **k):
        raise RuntimeError("recorder broke")
    f = frame([0])
    rec.offer("ABCD", at(1).to_pydatetime(), "not a frame", 1.0)        # a frame it cannot read: counted, not raised
    assert rec.errors == 1
    rec.offer = boom                                                   # whatever replaces offer may raise: still not the book's
    assert fs.tap(rec, lambda sym, now: f)("ABCD", at(1).to_pydatetime()) is f
    with pytest.raises(ZeroDivisionError):                             # the reader's own error is the reader's: passed on
        fs.tap(rec, lambda sym, now: 1 / 0)("ABCD", at(1).to_pydatetime())


def test_a_full_queue_drops_the_look_and_does_not_wait(tmp_path):
    rec = fs.Recorder(tmp_path / "shadow.db", queue_max=2)             # not started: the queue stays full
    for _ in range(5):
        rec.offer("ABCD", at(1).to_pydatetime(), frame([0]), 1.0)
    assert (rec.offered, rec.dropped, rec.q.qsize()) == (2, 3, 2)


def test_it_switches_itself_off_after_failed_writes_in_a_row(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, "FLUSH_S", 0.01)
    rec = fs.Recorder(tmp_path / "shadow.db")
    monkeypatch.setattr(rec, "_write", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("disk")))
    rec.start()
    for k in range(fs.MAX_FAILS + 3):
        rec.offer("ABCD", at(1, k).to_pydatetime(), frame([0]), 1.0)
        for _ in range(200):
            if not rec.q.qsize():
                break
            __import__("time").sleep(0.005)
    rec._thread.join(5)
    assert rec.on is False and rec.fails == fs.MAX_FAILS and not rec._thread.is_alive()
    before = rec.offered
    rec.offer("ABCD", at(2).to_pydatetime(), frame([0]), 1.0)          # off: nothing is even queued
    assert rec.offered == before


def test_a_database_that_cannot_be_opened_means_no_recording_and_no_error(tmp_path):
    (tmp_path / "blocked").write_text("a file where the folder should be")
    rec = fs.Recorder(tmp_path / "blocked" / "shadow.db").start()
    rec._thread.join(5)
    assert rec.on is False
    f = frame([0])
    assert fs.tap(rec, lambda sym, now: f)("ABCD", at(1).to_pydatetime()) is f


# ---- switching it off while the listener runs (owner 2026-11-27: "a safe, prompt recorder shutoff without
# restarting or delaying position management") ----
def _until(cond, seconds=5.0):
    import time
    end = time.monotonic() + seconds
    while time.monotonic() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_the_off_file_switches_a_running_recorder_off_and_the_reader_goes_on_untouched(tmp_path):
    rec = fs.Recorder(tmp_path / "shadow.db", watch_s=0.02).start()
    f = frame([0, 1])
    read = fs.tap(rec, lambda sym, now: f)
    assert read("ABCD", at(1).to_pydatetime()) is f and _until(lambda: rec.written == 1)
    rec.off_file.write_text("by hand")                                   # the whole switch: one file, no restart
    assert _until(lambda: not rec.on) and rec.why == "switched off by the OFF file"
    before = rec.offered
    assert read("ABCD", at(2).to_pydatetime()) is f and rec.offered == before and rec.q.qsize() == 0
    rec._thread.join(5)
    rec._watcher.join(5)
    assert not rec._thread.is_alive() and not rec._watcher.is_alive()    # both of its threads have ended
    assert rec.off_file.read_text() == "by hand"                         # a person's note is not overwritten
    c = sqlite3.connect(tmp_path / "shadow.db")
    assert dict(c.execute("SELECT key, value FROM meta WHERE key='off_why'"))["off_why"] == rec.why
    c.close()


def test_while_the_off_file_exists_a_restart_does_not_start_it(monkeypatch, tmp_path):
    monkeypatch.setenv(fs.ENV, "1")
    (tmp_path / fs.OFF_NAME).write_text("x")
    assert fs.start(tmp_path / "shadow.db") is None and not (tmp_path / "shadow.db").exists()
    reader = lambda sym, now: frame([0])                                 # noqa: E731
    assert fs.tap(fs.start(tmp_path / "shadow.db"), reader) is reader    # the book's reader, not even a wrapper
    (tmp_path / fs.OFF_NAME).unlink()
    rec = fs.start(tmp_path / "shadow.db")
    assert rec is not None and rec.on
    rec.stop()


def test_slow_offers_in_the_books_thread_switch_it_off_and_leave_the_reason(tmp_path):
    rec = fs.Recorder(tmp_path / "shadow.db", watch_s=0.02, slow_ms=-1.0, slow_max=3).start()   # every offer "slow"
    for k in range(3):
        assert rec.on
        rec.offer("ABCD", at(1, k).to_pydatetime(), frame([0]), 1.0)
    assert rec.on is False and rec.why == "3 offers within 600 s each took over -1 ms in the book's thread"
    assert _until(rec.off_file.exists) and rec.why in rec.off_file.read_text()       # and it stays off after a restart
    rec._thread.join(5)


def test_a_writer_that_cannot_keep_up_switches_it_off(tmp_path):
    rec = fs.Recorder(tmp_path / "shadow.db", queue_max=1, drop_max=3)   # not started: nothing empties the queue
    for k in range(4):
        rec.offer("ABCD", at(1, k).to_pydatetime(), frame([0]), 1.0)
    assert (rec.on, rec.dropped, rec.why) == (False, 3, "3 looks dropped within 600 s: its writer cannot keep up")
    rec.offer("ABCD", at(2).to_pydatetime(), frame([0]), 1.0)
    assert rec.dropped == 3                                              # off: not even counted any more


def test_the_two_trip_counts_are_per_window_so_rare_unrelated_stalls_never_add_up(tmp_path):
    """Review 2026-11-27: counted for ever, three stalls over weeks of uptime would have switched it off."""
    t = [0.0]

    def mono():                                              # each offer reads the clock twice: 1 s apart = "slow"
        t[0] += 1.0
        return t[0]
    rec = fs.Recorder(tmp_path / "shadow.db", queue_max=1, slow_ms=500.0, slow_max=3, drop_max=3, window_s=10.0,
                      mono=mono)                             # not started: the second look on is dropped
    for k in range(8):                                       # 2 slow offers (and 2 drops at most) per 10 s window
        rec.offer("ABCD", at(1, k).to_pydatetime(), frame([0]), 1.0)
        rec.offer("ABCD", at(1, k).to_pydatetime(), frame([0]), 1.0)
        t[0] += 20.0                                         # the next pair falls in a new window
    assert rec.on and (rec.slow, rec.dropped) == (16, 15)    # the totals are still counted, for the health row
    for k in range(3):                                       # three in ONE window: off
        rec.offer("ABCD", at(2, k).to_pydatetime(), frame([0]), 1.0)
    assert rec.on is False and rec.why.startswith("3 ")


@pytest.mark.parametrize("kw, why", [
    ({"max_db_bytes": 1}, "its database is "),
    ({"free": lambda p: 10}, f"under {fs.MIN_FREE_BYTES} bytes free on its volume"),
    ({"free": lambda p: 1 / 0}, "its watchdog could not look"),
])
def test_a_resource_budget_that_is_spent_switches_it_off(tmp_path, kw, why):
    rec = fs.Recorder(tmp_path / "shadow.db", watch_s=0.02, **kw).start()
    assert _until(lambda: not rec.on) and rec.why.startswith(why)
    assert _until(rec.off_file.exists)
    f = frame([0])
    assert fs.tap(rec, lambda sym, now: f)("ABCD", at(1).to_pydatetime()) is f       # the book reads on
    rec._thread.join(5)


def test_a_write_ahead_file_that_comes_and_goes_is_not_a_failure_to_look(tmp_path):
    rec = fs.Recorder(tmp_path / "shadow.db")
    assert rec._size() == 0                                              # neither file exists yet
    (tmp_path / "shadow.db").write_bytes(b"x" * 10)
    assert rec._size() == 10                                             # no write-ahead file: still a size
    (tmp_path / "shadow.db-wal").write_bytes(b"y" * 5)
    assert rec._size() == 15


def test_the_nightly_line_says_when_the_recorder_is_off(tmp_path):
    rec = fs.Recorder(tmp_path / "shadow.db", watch_s=0.02).start()
    rec.offer("ABCD", at(1).to_pydatetime(), frame([0]), 1.0)
    assert _until(lambda: rec.written == 1)
    rec._trip("3 offers each took over 250 ms in the book's thread")
    rec._thread.join(5)
    assert _until(rec.off_file.exists)
    _, _, health = job.recorded(tmp_path / "shadow.db", at(1).tz_convert("America/New_York").date().isoformat())
    assert health["off"]["why"] == "3 offers each took over 250 ms in the book's thread" and health["off"]["at"]


# ---- what is recorded ----
def test_the_first_closed_sighting_of_a_candle_and_the_look_before_it_are_kept(tmp_path):
    """Minute 2 (ends at 3:00). Looks at 2:50 (forming, held), 3:05 (NOT held), 3:20 (held closed, high revised)."""
    run(tmp_path, [(at(2, 50), "ABCD", frame([0, 1, 2])),
                   (at(3, 5), "ABCD", frame([0, 1])),
                   (at(3, 20), "ABCD", frame([0, 1, 2], bump={2: 10.6})),
                   (at(3, 35), "ABCD", frame([0, 1, 2], bump={2: 10.6}))])
    (b,) = rows(tmp_path / "shadow.db", "SELECT * FROM bars WHERE ts=?", at(2).isoformat())
    assert pd.Timestamp(b["first_at"]) == at(2, 50) and pd.Timestamp(b["closed_at"]) == at(3, 20)
    assert pd.Timestamp(b["closed_prev_look"]) == at(3, 5)             # a look after the minute ended that did not hold it
    assert (b["h"], b["lh"], b["revisions"]) == (10.6, 10.6, 1)        # values at the closed sighting; one change seen
    (first,) = rows(tmp_path / "shadow.db", "SELECT * FROM bars WHERE ts=?", at(0).isoformat())
    assert first["closed_prev_look"] is None and pd.Timestamp(first["closed_at"]) == at(2, 50)   # first look: censored
    assert len(rows(tmp_path / "shadow.db", "SELECT * FROM looks")) == 4
    (h,) = rows(tmp_path / "shadow.db", "SELECT * FROM health")
    assert (h["offered"], h["written"], h["dropped"], h["fails"]) == (4, 4, 0, 0)


def test_a_row_that_is_not_a_candle_is_left_out_and_the_look_is_still_recorded(tmp_path):
    f = frame([0, 1])
    f.loc[1, "high"] = float("nan")
    run(tmp_path, [(at(3), "ABCD", f), (at(3, 15), "EFGH", pd.DataFrame())])
    assert [b["ts"] for b in rows(tmp_path / "shadow.db", "SELECT ts FROM bars")] == [at(0).isoformat()]
    assert [(k["symbol"], k["n_bars"]) for k in rows(tmp_path / "shadow.db", "SELECT symbol, n_bars FROM looks ORDER BY id")] == [
        ("ABCD", 2), ("EFGH", 0)]


# ---- the comparison: what a missing live candle was ----
V = (10.0, 10.1, 9.9, 10.0, 100.0)


def live_row(closed, prev=None, values=V):
    return {"closed_at": closed.isoformat() if closed is not None else None,
            "closed_prev_look": prev.isoformat() if prev is not None else None, "values": values}


def test_each_watched_minute_is_classified_as_what_it_was():
    looks = [at(0, 30) + pd.Timedelta(seconds=15 * k) for k in range(0, 60)]          # 09:30:30 .. 09:45:15
    live = {at(1): live_row(at(2, 15), at(2, 0)),            # delivered: not held at 2:00, held at 2:15
            at(2): live_row(at(3, 0), at(2, 45)),            # delivered: the look before was before the minute ended
            at(5): live_row(at(6, 15), at(6, 0), values=(10.0, 10.4, 9.9, 10.0, 100.0)),
            at(6): live_row(at(7, 0), at(6, 45))}            # held live; the finished record does not have it
    free = {at(1): V, at(2): V, at(4): V, at(5): (10.0, 10.3, 9.9, 10.0, 100.0), at(8): V}
    tape = {at(1): V, at(2): V, at(3): V, at(4): V, at(5): (10.0, 10.2, 9.9, 10.0, 900.0), at(6): V}
    got = {r["ts"]: r for r in fs.classify(looks, live, free, tape, OPEN, CLOSE)}
    assert got[at(1)]["cls"] == "delivered" and (got[at(1)]["lag_lo"], got[at(1)]["lag_hi"]) == (0.0, 15.0)
    assert got[at(2)]["cls"] == "delivered" and (got[at(2)]["lag_lo"], got[at(2)]["lag_hi"]) == (None, 0.0)
    assert got[at(3)]["cls"] == "tape_only"                  # traded on the tape, not on the free feed's exchange
    assert got[at(4)]["cls"] == "never_held"                 # the free feed's finished record has it: missing data
    assert got[at(5)]["changed"] and got[at(5)]["outside_tape"]          # held 10.4, finished 10.3; the tape's high is 10.2
    assert got[at(6)]["cls"] == "live_only"
    late_volume = fs.classify(looks, {at(1): live_row(at(2, 15), at(2, 0), values=(10.0, 10.1, 9.9, 10.0, 60.0))},
                              {at(1): V}, {at(1): V}, OPEN, CLOSE)[1]
    assert late_volume["ts"] == at(1) and late_volume["changed"] is False and late_volume["volume_changed"] is True
    assert got[at(7)]["cls"] == "no_trade_on_tape"           # a genuine no-trade minute, on both finished records
    assert got[at(8)]["cls"] == "never_held" and got[at(8)]["free_not_on_tape"]
    assert at(0) in got and at(14) not in got                # watched: a look before its end AND 60 s after it
    s = fs.summarize([{**r, "symbol": "ABCD"} for r in got.values()])
    assert s["no_live_candle_was"] == {"no_trade_on_tape": sum(r["cls"] == "no_trade_on_tape" for r in got.values()),
                                       "tape_only": 1, "never_held": 2}
    assert s["no_live_candle"] == sum(s["no_live_candle_was"].values()) and s["delivered"] == 3
    assert s["proved_later_than_s"]["5"] == 0 and s["held_within_s"]["15"] == 3 and s["lag_hi_s"]["max"] == 15.0
    assert s["free_feed_share_of_tape_minutes"] == round(5 / 6, 4)       # 3 delivered + 2 never held, of 6 traded minutes


def test_a_minute_is_not_watched_unless_the_book_looked_before_it_ended_and_a_minute_after():
    assert fs.classify([], {}, {}, {}, OPEN, CLOSE) == []
    one = fs.classify([at(5, 10), at(5, 25)], {}, {}, {at(5): V}, OPEN, CLOSE)
    assert one == []                                          # no look 60 s after any minute's end
    late = fs.classify([at(5, 10), at(8, 10)], {}, {}, {}, OPEN, at(7))  # an early close at minute 7: nothing after it
    assert [r["ts"] for r in late] == [at(5), at(6)]


def test_a_censored_first_look_is_left_out_of_the_timing():
    looks = [at(0, 30), at(2, 30), at(4, 0)]
    live = {at(0): live_row(at(2, 30), None), at(1): live_row(at(2, 30), at(0, 30))}
    got = fs.classify(looks, live, {at(0): V, at(1): V}, {at(0): V, at(1): V}, OPEN, CLOSE)
    s = fs.summarize([{**r, "symbol": "ABCD"} for r in got])
    assert [r.get("censored") for r in got[:2]] == [True, False] and s["delivered"] == 2 and s["delivered_timed"] == 1


def test_stop_detection_is_the_delay_from_the_tapes_minute_to_the_look_that_held_it():
    tape = {at(3): V, at(4): (10.0, 10.1, 9.4, 9.5, 500.0), at(5): V}                 # the tape trades 9.4 in minute 4
    live = {at(4): live_row(at(5, 10), at(4, 55)),                                    # the live candle of minute 4: low 9.9
            at(6): live_row(at(7, 5), at(6, 50), values=(9.6, 9.7, 9.3, 9.4, 50.0))}  # first live candle through 9.5
    got = fs.stop_seen(at(3), 9.5, "long", live, tape, CLOSE)
    assert got == {"tape_minute": at(4).isoformat(), "live_minute": at(6).isoformat(),
                   "live_seen_at": at(7, 5).isoformat(), "lag_s": 125.0}
    never = fs.stop_seen(at(3), 9.5, "long", {at(4): live_row(at(5, 10))}, tape, CLOSE)
    assert (never["tape_minute"], never["live_minute"], never["lag_s"]) == (at(4).isoformat(), None, None)
    assert fs.stop_seen(at(3), 9.0, "long", live, tape, CLOSE)["tape_minute"] is None


# ---- the nightly job, end to end on a recorder's own database ----
def test_the_job_reads_the_recorders_database_and_writes_one_line_a_session(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    looks = [(at(0, 30) + pd.Timedelta(seconds=15 * k), "ABCD",
              frame([m for m in (0, 1, 2, 4) if at(m) + fs.BAR + pd.Timedelta(seconds=3) <= at(0, 30) + pd.Timedelta(seconds=15 * k)]))
             for k in range(0, 40)]
    run(tmp_path, looks)
    px = (10.0, 10.1, 9.9, 10.0, 100.0)
    asked = []

    def fetch(feed, symbols, start, end):
        asked.append((feed, tuple(symbols)))
        return ({"ABCD": {at(m): px for m in ((0, 1, 2, 4) if feed == "iex" else (0, 1, 2, 3, 4))}}, [])
    o = job.compare(DAY, tmp_path / "shadow.db", tmp_path / "no-ops.db", fetch=fetch)
    assert asked == [("iex", ("ABCD",)), ("sip", ("ABCD",))]
    assert (o["symbols"], o["looks"], o["delivered"], o["tape_only"], o["never_held"], o["changed"]) == (1, 40, 4, 1, 0, 0)
    assert o["lag_hi_s"]["max"] <= 15.0 and o["health"]["dropped"] == 0 and o["long_stops"] == []
    job.write(o, tmp_path / "daily.jsonl")
    job.write({**o, "delivered": 99}, tmp_path / "daily.jsonl")        # a second run replaces the session's line
    (line,) = (tmp_path / "daily.jsonl").read_text().splitlines()
    assert json.loads(line)["delivered"] == 99 and "feed shadow 2026-11-09" in job.words(o)
    empty = job.compare("2026-11-10", tmp_path / "shadow.db", tmp_path / "no-ops.db", fetch=fetch)
    assert empty["note"] == "no look was recorded for this session" and "no look" in job.words(empty)


# ---- the frozen after-the-session check (proposal section 4a) ----
def gaps(tmp_path, lines):
    p = tmp_path / "loop_gaps.jsonl"
    p.write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n")
    return p


def summary(minute, n=40, counts=(40, 0, 0, 0), work=0.1, loop="long book", day=OPEN):
    return {"at": (day + pd.Timedelta(minutes=minute, seconds=17)).isoformat(), "loop": loop, "kind": "summary", "n": n,
            "counts": list(counts), "max_gap": 15.1, "max_work": work, "every": 15.0}


def whole(work=0.1, minutes=range(10, 390, 10), day=OPEN, **kw):
    """One row every 10 minutes over the window: 38 rows in a full session, stamped 09:40:17 to 15:50:17 ET."""
    return [summary(m, work=work, day=day, **kw) for m in minutes]


def test_the_tolerance_is_the_table_written_before_the_recorder_ran():
    """Pinned: these four numbers are section 4a's table of 2026-11-27 and are not moved after a result is seen. The
    rule for a whole record (no stretch of two row intervals without a row) is not a tolerance and is pinned too."""
    assert job.TOL == {"within_min": 0.995, "late_max": 0, "work_p90_max_s": 0.2, "work_max_max_s": 0.5}
    assert (job.LOOP, str(job.PACE)) == ("long book", "reports/ops/loop_gaps.jsonl")
    from sgt.ops import own_thread
    assert (job.HOLE_S, own_thread.FLUSH_S) == (1200.0, 600.0)


def test_the_long_books_own_cadence_record_is_read_for_the_sessions_regular_hours_only(tmp_path):
    work = [0.1] * 33 + [0.2, 0.2, 0.2, 0.2, 0.4]
    p = gaps(tmp_path, [summary(5, n=999, counts=(0, 0, 0, 999), work=9.0)] + [   # stamped before 09:40
        summary(10 + 10 * k, work=w) for k, w in enumerate(work)] + [
        summary(391, n=999, counts=(0, 0, 0, 999), work=9.0),                  # stamped after the close
        summary(60, n=999, counts=(0, 0, 0, 999), work=9.0, loop="stop check"),   # another loop
        summary(60, n=999, counts=(0, 0, 0, 999), work=9.0, day=OPEN + pd.Timedelta(days=1)),   # another day
        {"at": (OPEN + pd.Timedelta(hours=8)).isoformat(), "loop": "long book", "gap": 61.0, "every": 15.0},   # late, at night
        "torn line after the session"])                                        # its neighbours place it after the close
    o = job.pace(DAY, p)
    assert (o["passes"], o["counts"], o["late"], o["periods"], o["unreadable_lines"]) == (1520, [1520, 0, 0, 0], 0, 38, 0)
    assert (o["within"], o["work_p90_s"], o["work_max_s"]) == (1.0, 0.2, 0.4)      # sorted[int(0.9 * 37)]: the baseline's rule
    assert o["window_et"] == "09:40-16:00" and o["full_session"] is True
    assert o["whole"] is True and o["longest_stretch_without_a_row_s"] == 642 and "incomplete" not in o
    assert o["inside"] == {"cadence": True, "late": True, "work_p90": True, "work_max": True} and job.verdict(o["inside"]) == "inside"
    assert o["tolerance"] is job.TOL and "not proof of no effect" in o["limits"]


@pytest.mark.parametrize("change, outside", [
    ({0: summary(10, n=1000, counts=(995, 5, 0, 0), work=0.2), 1: summary(20, n=0, counts=(0, 0, 0, 0), work=0.5)}, None),
    ({0: summary(10, n=40000, counts=(39000, 1000, 0, 0))}, ["cadence"]),
    ({k: summary(10 + 10 * k, work=0.6) for k in range(5)}, ["work_p90", "work_max"]),     # sorted[33] of 38
    ({3: summary(40, work=0.6)}, ["work_max"]),
    ({}, ["late"]),
])
def test_a_figure_at_the_limit_is_inside_and_one_past_it_is_outside_by_name(tmp_path, change, outside):
    rows_ = whole(work=0.2 if outside is None else 0.1)
    for k, r in change.items():
        rows_[k] = r
    if outside == ["late"]:
        rows_.append({"at": at(45).isoformat(), "loop": "long book", "gap": 36.0, "every": 15.0})
    o = job.pace(DAY, gaps(tmp_path, rows_))
    assert o["whole"] and [k for k, v in o["inside"].items() if v is False] == (outside or [])
    assert job.verdict(o["inside"]) == ("outside: " + ", ".join(outside) if outside else "inside")


def test_a_record_that_is_missing_or_empty_is_not_measured_and_never_a_pass(tmp_path):
    raw = tmp_path / "bytes.jsonl"
    raw.write_bytes(b"\xff\xfe\x00not text")
    for o in (job.pace(DAY, tmp_path / "none.jsonl"), job.pace(DAY, gaps(tmp_path, [summary(60, loop="stop check")])),
              job.pace(DAY, raw)):
        assert o["note"] and set(o["inside"].values()) == {None}
        assert job.verdict(o["inside"]) == "not measured: cadence, late, work_p90, work_max"


def test_a_record_with_a_row_missing_cannot_say_inside_and_still_says_outside(tmp_path):
    """Owner 2026-11-27: partial sessions, missing observations and too little timing data are said, never passed.
    A listener restart or a full record leaves a stretch of the window without a row: every figure that would have
    been inside is 'not measured'; a figure that is outside on the rows that exist stays outside, by name."""
    o = job.pace(DAY, gaps(tmp_path, whole(minutes=range(10, 130, 10))))             # rows until 11:30 only
    assert o["whole"] is False and o["periods"] == 12 and o["longest_stretch_without_a_row_s"] == 16242
    assert set(o["inside"].values()) == {None} and "longest stretch of the window without a row 271 min" in o["incomplete"]
    assert job.verdict(o["inside"]) == "not measured: cadence, late, work_p90, work_max"
    one = job.pace(DAY, gaps(tmp_path, whole(minutes=[m for m in range(10, 390, 10) if m not in (200, 210)])))
    assert one["whole"] is False and one["periods"] == 36                             # two rows in a row are missing
    assert job.pace(DAY, gaps(tmp_path, whole(minutes=[m for m in range(10, 390, 10) if m != 200])))["whole"] is False
    bad = job.pace(DAY, gaps(tmp_path, whole(minutes=range(10, 130, 10)) + [summary(125, work=0.9)]))
    assert bad["inside"] == {"cadence": None, "late": None, "work_p90": None, "work_max": False}
    assert job.verdict(bad["inside"]) == "outside: work_max; not measured: cadence, late, work_p90"
    said = job.report({"session": DAY, "pace": bad, "integrity": {}, "longs_entered": 0})
    assert said.startswith("⚠️ feed recorder 2026-11-09: timing OUTSIDE: longest pass (maximum); not measured: passes on "
                           "time, late passes, longest pass (90th percentile); integrity not measured. ")
    assert "maximum 0.9 s (limit 0.5)" in said and "the cadence record is not whole" in said and job.POLICY in said


def test_a_line_that_cannot_be_read_inside_the_window_means_not_measured(tmp_path):
    rows_ = whole()
    torn = rows_[:20] + ["{\"at\": \"2026-11-09T18:0", json.dumps([1, 2])] + rows_[20:] + [
        json.dumps({"at": at(30).isoformat(), "loop": "long book", "kind": "summary", "n": "x"})]
    o = job.pace(DAY, gaps(tmp_path, torn))
    assert o["unreadable_lines"] == 3 and o["whole"] is False and set(o["inside"].values()) == {None}
    assert "3 lines that cannot be read and may belong to the window" in o["incomplete"]
    before = job.pace(DAY, gaps(tmp_path, ["torn", summary(-60)] + rows_))            # torn before a pre-market row
    assert before["unreadable_lines"] == 0 and before["whole"] is True and job.verdict(before["inside"]) == "inside"
    naive = job.pace(DAY, gaps(tmp_path, rows_ + [{"at": "2026-11-09T18:00:00", "loop": "long book", "gap": 99.0}]))
    assert naive["unreadable_lines"] == 1 and naive["late"] == 0 and naive["whole"] is False   # a stamp with no zone


def test_an_early_close_is_labelled_and_is_not_the_comparison_session(tmp_path):
    half = pd.Timestamp("2026-11-27 14:30", tz="UTC")
    o = job.pace("2026-11-27", gaps(tmp_path, whole(minutes=range(10, 210, 10), day=half)
                                    + [summary(215, n=7, counts=(0, 0, 0, 7), day=half)]))
    assert (o["window_et"], o["full_session"], o["passes"], o["whole"]) == ("09:40-13:00", False, 800, True)   # 13:05: after its close
    assert "(not a full session)" in job.pace_words({"pace": o, "longs_entered": 0})
    assert job.report({"session": "2026-11-27", "pace": o, "integrity": {}}).startswith(
        "feed recorder 2026-11-27 (not a full session): timing inside; integrity not measured. 800 passes")


def test_the_integrity_row_says_what_failed_and_what_was_not_measured():
    good = {"health": {"dropped": 0, "fails": 0}, "watched_minutes": 5, **{k: 1 for k in fs.CLASSES}}
    assert job.verdict(job.integrity(good)) == "inside"
    assert job.verdict(job.integrity({**good, "health": {"dropped": 2, "fails": 0, "off": {"why": "x"}}})) == "outside: dropped_0, not_switched_off"
    assert job.verdict(job.integrity({**good, "delivered": 3})) == "outside: counts_add_up"
    assert job.verdict(job.integrity({"health": {}})) == "not measured: dropped_0, write_fails_0, not_switched_off, counts_add_up"
    half = job.integrity({"health": {"dropped": 0, "fails": 3}})                 # a failed check never hides an unknown one
    assert job.verdict(half) == "outside: write_fails_0; not measured: counts_add_up"


def test_the_nightly_line_carries_the_check_with_each_figure_beside_its_limit(tmp_path):
    looks = [(at(0, 30) + pd.Timedelta(seconds=15 * k), "ABCD", frame([0] if k > 6 else [])) for k in range(0, 20)]
    run(tmp_path, looks)
    px = (10.0, 10.1, 9.9, 10.0, 100.0)
    rows_ = whole()
    rows_[5] = summary(60, work=0.3)
    p = gaps(tmp_path, rows_)
    o = job.compare(DAY, tmp_path / "shadow.db", tmp_path / "no-ops.db", fetch=lambda *a: ({"ABCD": {at(0): px}}, []), gaps=p)
    assert o["pace"]["passes"] == 1520 and o["longs_entered"] == 0 and job.verdict(o["integrity"]) == "inside"
    said = job.words(o)
    assert ("frozen check, long book 09:40-16:00 ET: 1520 passes, 100.00% started within 1.1 x 15 s (limit at least 99.5%), "
            "late 0 (limit 0), longest pass per 10 minutes: 90th percentile 0.1 s (limit 0.2), maximum 0.3 s (limit 0.5), "
            "38 ten-minute rows, 0 longs entered; timing inside; integrity inside") in said
    assert o["report"] == (                                              # the one line the midnight summary text carries
        "feed recorder 2026-11-09: timing inside; integrity inside. 1520 passes, 100.00% on time (limit at least 99.5%), "
        "late 0 (limit 0), longest pass per 10 min: 90th percentile 0.1 s (limit 0.2), maximum 0.3 s (limit 0.5), "
        "0 longs entered. The record resolves 0.1 s per 10 minutes: inside is not proof of no effect.")
    json.dumps(o)                                                        # the line is written as JSON
    empty = job.compare("2026-11-10", tmp_path / "shadow.db", tmp_path / "no-ops.db", fetch=None, gaps=tmp_path / "none.jsonl")
    assert "no look was recorded" in job.words(empty) and "timing not measured" in job.words(empty)
    assert empty["report"].startswith("feed recorder 2026-11-10: timing not measured: passes on time, late passes, ")
    assert "; integrity not measured: " in empty["report"] and "not proof of no effect" not in empty["report"]
    assert "the cadence record could not be read (FileNotFoundError)" in empty["report"] and job.POLICY not in empty["report"]


GOOD = {"dropped_0": True, "write_fails_0": True, "not_switched_off": True, "counts_add_up": True}


def breached(tmp_path, work=0.7, **more):
    rows_ = whole()
    rows_[7] = summary(80, work=work)
    return {"session": DAY, "pace": job.pace(DAY, gaps(tmp_path, rows_)), "longs_entered": 3, "integrity": dict(GOOD), **more}


def test_an_outside_figure_is_named_with_what_was_observed_and_its_limit(tmp_path):
    o = breached(tmp_path)
    assert job.breaches(o) == [{"check": "work_max", "metric": "longest pass (maximum)", "observed": "0.7 s", "limit": "0.5 s"}]
    assert job.report(o) == (
        "⚠️ feed recorder 2026-11-09: timing OUTSIDE: longest pass (maximum); integrity inside. Outside: longest pass "
        "(maximum) 0.7 s (limit 0.5 s). A confirmed breach switches the recorder off (the nightly job creates its OFF "
        "file); only a person puts it back on. 1520 passes, 100.00% on time (limit at least 99.5%), late 0 (limit 0), "
        "longest pass per 10 min: 90th percentile 0.1 s (limit 0.2), maximum 0.7 s (limit 0.5), 3 longs entered.")
    every = {"session": DAY, "pace": job.pace(DAY, gaps(tmp_path, [summary(10, n=1000, counts=(900, 98, 0, 2), work=0.9)] + whole()[1:] + [
        {"at": at(45).isoformat(), "loop": "long book", "gap": 36.0, "every": 15.0}])),
        "health": {"dropped": 4, "fails": 2, "off": {"why": "500 looks dropped within 600 s"}}, "watched_minutes": 40,
        **{k: 9 for k in fs.CLASSES}, "integrity": {k: False for k in GOOD}}
    assert [(b["check"], b["observed"], b["limit"]) for b in job.breaches(every)] == [
        ("cadence", "95.97%", "at least 99.5%"), ("late", "1", "0"), ("work_max", "0.9 s", "0.5 s"), ("dropped_0", "4", "0"),
        ("write_fails_0", "2", "0"), ("not_switched_off", "500 looks dropped within 600 s", "not switched off"),
        ("counts_add_up", "45 classified of 40 watched", "equal")]
    assert set(job.PLAIN) == set(o["pace"]["inside"]) | set(o["integrity"])
    assert job.breaches({"session": DAY, "integrity": {"counts_add_up": False}}) == [                 # the figure is gone:
        {"check": "counts_add_up", "metric": "class counts", "observed": "figure unavailable", "limit": "see the session's line"}]


def test_a_confirmed_breach_switches_a_running_recorder_off_and_touches_nothing_else(tmp_path):
    """Owner 2026-11-27: "On a confirmed tolerance breach, atomically create the recorder's OFF file." The recorder
    runs with production settings; the job's switch is the file; the recorder's own watchdog does the rest."""
    db = tmp_path / "rec" / "shadow.db"
    rec = fs.Recorder(db).start()
    try:
        assert _until(lambda: db.exists()) and rec.on
        o = breached(tmp_path)
        rows_ = job._pace(DAY, tmp_path / "loop_gaps.jsonl")[1]
        sh = job.shutoff(o, db, rows_, tmp_path / "evidence")
        assert sh["result"] == "created" and sh["confirmed_after_s"] is not None and sh["confirmed_after_s"] <= 6
        assert rec.on is False and rec.why == "switched off by the OFF file"
        said = (db.parent / "OFF").read_text()
        assert said.endswith("switched off by the nightly check of session 2026-11-09: longest pass (maximum) 0.7 s (limit 0.5 s)\n")
        assert sorted(f.name for f in db.parent.iterdir() if "OFF" in f.name) == ["OFF"]             # no temporary file left
        kept = json.loads((tmp_path / "evidence" / "2026-11-09.json").read_text())
        assert len(kept["cadence_rows"]) == 38 and kept["line"]["pace"]["work_max_s"] == 0.7 and sh["evidence"].endswith("2026-11-09.json")
        o["shutoff"] = sh
        text = job.report(o)
        assert ("Outside: longest pass (maximum) 0.7 s (limit 0.5 s). RECORDER SWITCHED OFF by this check at ") in text
        assert "OFF file created; the recorder confirmed off after " in text and "It stays off until a person removes the file." in text
        again = job.shutoff(o, db, rows_, tmp_path / "evidence")                                     # the file stands
        assert again["result"] == "already present" and (db.parent / "OFF").read_text() == said
        assert again["evidence"].endswith("(already kept)")
        assert "OFF file was already there and is left as it is" in job.report({**o, "shutoff": again})
    finally:
        rec.stop()
    assert fs.start(db) is None                                                                      # nothing puts it back on


def test_not_measured_alone_never_switches_and_a_breach_beside_an_unknown_does(tmp_path):
    db = tmp_path / "rec" / "shadow.db"
    unknown = {"session": DAY, "pace": job.pace(DAY, tmp_path / "none.jsonl"), "integrity": {k: None for k in GOOD}}
    partial = {"session": DAY, "pace": job.pace(DAY, gaps(tmp_path, whole(minutes=range(10, 130, 10)))), "integrity": dict(GOOD)}
    for o in (unknown, partial, {"session": DAY, "pace": job.pace(DAY, gaps(tmp_path, whole())), "integrity": dict(GOOD)}):
        assert job.breaches(o) == [] and job.shutoff(o, db, [], tmp_path / "evidence") is None
    assert not (db.parent / "OFF").exists() and not (tmp_path / "evidence").exists()
    both = {"session": DAY, "integrity": {k: None for k in GOOD},                                    # one outside, the rest unknown
            "pace": job.pace(DAY, gaps(tmp_path, whole(minutes=range(10, 130, 10)) + [summary(125, work=0.9)]))}
    assert both["pace"]["inside"] == {"cadence": None, "late": None, "work_p90": None, "work_max": False}
    sh = job.shutoff(both, db, [], tmp_path / "evidence", wait_s=1.0, sleep=lambda s: None)
    assert sh["result"] == "unconfirmed" and sh["made"] == "created" and (db.parent / "OFF").exists()         # no recorder runs here
    text = job.report({**both, "shutoff": sh})
    assert text.startswith("⚠️ ") and "SHUTOFF UNCONFIRMED at " in text and "RECORDER SWITCHED OFF" not in text
    assert ("the OFF file is in place, but the running recorder did not acknowledge within 10 s. It cannot start again while "
            "the file exists; whether it still records in the running process is not known. No trading process was stopped.") in text


def test_a_recorder_that_is_already_off_is_left_as_it_is_and_no_file_is_made(tmp_path):
    """Its counters stay as they were while it is off, so the next night's check finds the same figures outside. A
    person who removed the file to bring the recorder back at the next deploy must not find it made again."""
    db = tmp_path / "rec" / "shadow.db"
    o = breached(tmp_path, health={"dropped": 500, "fails": 0, "off": {"at": "2026-11-09T18:00:00+00:00", "why": "500 looks dropped within 600 s"}},
                 integrity={**GOOD, "dropped_0": False, "not_switched_off": False})
    sh = job.shutoff(o, db, [], tmp_path / "evidence")
    assert sh["result"] == "already off" and sh["file_present"] is False and len(sh["breaches"]) == 3
    assert not db.parent.exists() and not (tmp_path / "evidence").exists()
    text = job.report({**o, "shutoff": sh})
    assert ("The recorder was already off (off since 2026-11-09T18:00:00+00:00: 500 looks dropped within 600 s): nothing was "
            "switched; OFF file not present: it stays off until its process restarts.") in text


@pytest.mark.parametrize("error", [PermissionError("no"), OSError(28, "No space left on device")])
def test_a_switch_that_cannot_be_made_says_the_recorder_is_still_on(tmp_path, monkeypatch, error):
    from sgt.fast import feed_shadow_check as check

    def fail(*a, **k):
        raise error
    monkeypatch.setattr(check.os, "link", fail)
    db = tmp_path / "rec" / "shadow.db"
    o = breached(tmp_path)
    sh = job.shutoff(o, db, [], tmp_path / "evidence", wait_s=0.0)
    assert sh["result"] == "failed" and type(error).__name__ in sh["error"] and not (db.parent / "OFF").exists()
    assert [f.name for f in db.parent.iterdir()] == []                                               # no half-made file
    assert sh["evidence"].startswith("not kept (")                                                   # said, and the switch was still tried
    text = job.report({**o, "shutoff": sh})
    assert "SHUTOFF FAILED at " in text and "THE RECORDER IS STILL ON. Create reports/feed_shadow/OFF on the runner by hand." in text


def recorder_db(path, off=False):
    """The recorder's own database as the job reads it: ``off`` = it has said that it is off (its writer's last act)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
    c.execute("DELETE FROM meta")
    if off:
        c.execute("INSERT INTO meta VALUES ('off_at', '2026-11-10T02:40:03+00:00')")
    c.commit()
    c.close()


@pytest.fixture
def night(tmp_path, monkeypatch):
    """The nightly job on a runner of its own: ``night.run(session, work)`` is one scheduled run for a session whose
    longest pass was ``work`` seconds; ``night.answers`` = whether the recorder acknowledges the OFF file."""
    from sgt.fast import feed_shadow_check as check

    class Night:
        db = tmp_path / "feed_shadow" / "shadow.db"
        off = tmp_path / "feed_shadow" / "OFF"
        answers, slept = True, []

        def line(self, session):
            return job.line_of(session, job.OUT)

        def run(self, session, work=0.1, named=False):
            monkeypatch.setattr(job, "last_session", lambda now: session)
            monkeypatch.setattr(job, "compare", lambda sess, *a: breached(tmp_path, work=work, session=sess, note="stub"))
            return job.main(["compare", "--session", session] if named else ["compare"])
    n = Night()
    recorder_db(n.db)

    def nap(seconds):
        n.slept.append(seconds)
        if n.answers and n.off.exists():
            recorder_db(n.db, off=True)
    monkeypatch.setattr(fs, "DB", n.db)
    monkeypatch.setattr(job, "OUT", tmp_path / "feed_shadow" / "daily.jsonl")
    monkeypatch.setattr(job, "EVIDENCE", tmp_path / "feed_shadow" / "evidence")
    monkeypatch.setattr(job, "PACE", tmp_path / "loop_gaps.jsonl")
    monkeypatch.setattr(job, "shutoff", lambda o, d, rows, ev, found=None: check.shutoff(o, d, rows, ev, 2.0, nap, found))
    monkeypatch.setattr(job, "recheck", lambda prev, d: check.recheck(prev, d, 2.0, nap))
    return n


def test_the_scheduled_run_switches_once_and_a_named_run_or_a_second_run_never_does(night):
    """Never re-enabled by the job, and never switched off twice for the same session: after a person removes the
    file, a second run of the same session's check (a closed day) leaves the recorder as the person left it."""
    assert night.run(DAY, 0.7, named=True) == 0 and not night.off.exists()                           # named: reports only
    assert night.line(DAY)["shutoff"]["result"] == "not attempted" and "was not switched by this run" in night.line(DAY)["report"]
    assert night.run(DAY, 0.7) == 0 and night.off.exists()                                           # the nightly run
    line = night.line(DAY)
    assert line["shutoff"]["result"] == "created" and line["shutoff"]["confirmed_after_s"] == 0.5
    assert "RECORDER SWITCHED OFF by this check" in line["report"] and "confirmed off after 0.5 s" in line["report"]
    assert len(line["shutoff"]["breaches"]) == 1 and (job.EVIDENCE / f"{DAY}.json").exists()
    night.off.unlink()                                                                               # a person puts it back on
    recorder_db(night.db)                                                                            # and it runs again
    assert night.run(DAY, 0.7) == 0 and not night.off.exists()                                       # same session again
    assert night.line(DAY)["shutoff"]["repeated"] is False
    assert night.run("2026-11-10", 0.1) == 0 and not night.off.exists()                              # a clean session: nothing
    assert "shutoff" not in night.line("2026-11-10")
    assert night.run("2026-11-11", 0.7) == 0 and night.off.exists()                                  # a NEW breach: switched again


def test_a_switch_the_running_recorder_does_not_acknowledge_is_unconfirmed_and_fails_the_job(night, tmp_path):
    """Owner 2026-11-27 (night): no acknowledgment within 10 s is "shutoff unconfirmed", not success, and goes out
    through the job-failure path. Later runs only look again; the file is never made twice."""
    night.answers = False
    assert night.run(DAY, 0.7) == 1 and night.off.exists()                                           # the job FAILS: texted
    sh = night.line(DAY)["shutoff"]
    assert (sh["result"], sh["made"], sh["nights"]) == ("unconfirmed", "created", 1)
    assert "SHUTOFF UNCONFIRMED" in night.line(DAY)["report"] and "The next scheduled run tries again (run 1 of 5)." in night.line(DAY)["report"]
    said = night.off.read_text()
    assert night.run("2026-11-10", 0.1) == 1 and night.off.read_text() == said                       # next night: looked at again
    line = night.line("2026-11-10")
    assert (line["shutoff"]["result"], line["shutoff"]["pending_from"], line["shutoff"]["nights"]) == ("unconfirmed", DAY, 2)
    assert line["report"].startswith("⚠️ feed recorder 2026-11-10: timing inside; integrity inside. Switch still open from session "
                                     "2026-11-09 (longest pass (maximum) 0.7 s (limit 0.5 s)): SHUTOFF UNCONFIRMED at ")
    night.answers = True                                                                             # now it answers
    recorder_db(night.db, off=True)
    assert night.run("2026-11-11", 0.1) == 0
    line = night.line("2026-11-11")
    assert (line["shutoff"]["result"], line["shutoff"]["pending_from"], line["shutoff"]["nights"]) == ("created", DAY, 3)
    assert night.run("2026-11-12", 0.1) == 0 and "shutoff" not in night.line("2026-11-12")           # closed: nothing more


def test_a_person_who_removes_the_file_of_an_unconfirmed_switch_is_never_overruled(night):
    night.answers = False
    assert night.run(DAY, 0.7) == 1 and night.off.exists()
    night.off.unlink()                                                                               # the owner's re-enable
    assert night.run(DAY, 0.7) == 0 and not night.off.exists()                                       # same session, a second run
    assert night.line(DAY)["shutoff"]["result"] == "removed by a person"
    night.answers = False
    assert night.run("2026-11-10", 0.1) == 0 and not night.off.exists() and "shutoff" not in night.line("2026-11-10")


def test_a_failed_file_is_tried_three_times_a_run_and_on_the_following_runs_five_in_all(night, monkeypatch):
    """"Once per session" stops a second successful switch, not a retry after a failed one: bounded both ways."""
    from sgt.fast import feed_shadow_check as check

    real, calls = check.os.link, []

    def refuse(a, b):
        calls.append(b)
        raise PermissionError("no")
    monkeypatch.setattr(check.os, "link", refuse)
    assert night.run(DAY, 0.7) == 1 and not night.off.exists()
    sh = night.line(DAY)["shutoff"]
    assert (sh["result"], sh["attempts"], sh["nights"]) == ("failed", 3, 1) and night.slept == [2.0, 2.0]
    assert [Path(c).name for c in calls] == [f"{DAY}.json", "OFF", "OFF", "OFF"]                      # the evidence, then three tries
    assert "SHUTOFF FAILED at " in night.line(DAY)["report"] and "after 3 tries (PermissionError: no): THE RECORDER IS STILL ON" in night.line(DAY)["report"]
    assert night.run(DAY, 0.7) == 1 and len(calls) == 8                                              # same session again: retried
    for k, day in enumerate(("2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"), start=2):      # clean sessions after it
        assert night.run(day, 0.1) == 1
        sh = night.line(day)["shutoff"]
        assert (sh["result"], sh["pending_from"], sh["nights"]) == ("failed", DAY, k)
    assert len(calls) == 24 and "No further automatic retry: act by hand." in night.line("2026-11-13")["report"]
    assert "The next scheduled run tries again (run 4 of 5)." in night.line("2026-11-12")["report"]
    assert night.run("2026-11-16", 0.1) == 0 and len(calls) == 24 and "shutoff" not in night.line("2026-11-16")   # bounded


def test_a_file_that_failed_one_night_is_made_on_the_next_run(night, monkeypatch):
    from sgt.fast import feed_shadow_check as check

    real = check.os.link
    monkeypatch.setattr(check.os, "link", lambda a, b: (_ for _ in ()).throw(OSError(28, "No space left on device")))
    assert night.run(DAY, 0.7) == 1 and not night.off.exists()
    monkeypatch.setattr(check.os, "link", real)                                                      # room again
    assert night.run("2026-11-10", 0.1) == 0 and night.off.exists()
    line = night.line("2026-11-10")
    assert (line["shutoff"]["result"], line["shutoff"]["pending_from"], line["shutoff"]["nights"]) == ("created", DAY, 2)
    assert "switched off by the nightly check of session 2026-11-09: longest pass (maximum) 0.7 s (limit 0.5 s)" in night.off.read_text()
    assert "Switch still open from session 2026-11-09 (longest pass (maximum) 0.7 s (limit 0.5 s)): RECORDER SWITCHED OFF" in line["report"]
    assert json.loads((job.EVIDENCE / f"{DAY}.json").read_text())["line"]["session"] == DAY           # the breach's own evidence
    night.off.unlink()                                                                               # a person re-enables
    recorder_db(night.db)
    assert night.run("2026-11-11", 0.1) == 0 and not night.off.exists()                              # a made file is never made again


def test_one_refusal_inside_a_run_is_retried_and_a_file_already_there_needs_the_recorders_word_too(tmp_path, monkeypatch):
    from sgt.fast import feed_shadow_check as check

    db, real, n = tmp_path / "rec" / "shadow.db", check.os.link, []
    recorder_db(db, off=True)

    def twice(a, b):
        n.append(1)
        if len(n) < 3:
            raise OSError(5, "Input/output error")
        return real(a, b)
    monkeypatch.setattr(check.os, "link", twice)
    sh = job.shutoff(breached(tmp_path), db, [], tmp_path / "evidence", sleep=lambda s: None)
    assert (sh["result"], sh["attempts"], sh["confirmed_after_s"]) == ("created", 2, 0.0)             # the evidence took the first refusal
    assert sh["evidence"].startswith("not kept (OSError")
    assert sorted(f.name for f in db.parent.iterdir() if "OFF" in f.name) == ["OFF"]
    again = job.shutoff(breached(tmp_path), db, [], tmp_path / "evidence", sleep=lambda s: None)     # there, and it says off
    assert again["result"] == "already present" and "the recorder confirms it is off" in job.report({**breached(tmp_path), "shutoff": again})
    recorder_db(db)                                                                                  # there, but it says nothing
    quiet = job.shutoff(breached(tmp_path), db, [], tmp_path / "evidence", wait_s=1.0, sleep=lambda s: None)
    assert (quiet["result"], quiet["made"]) == ("unconfirmed", "already present")


def test_the_failure_text_says_what_happened_to_the_switch(tmp_path):
    """The existing job-failure text (scripts/ops_notify.py), for this job only when its switch is the reason."""
    import ops_notify

    log = tmp_path / "run.log"
    log.write_text('=== 2026-11-09 21:40:00 EST python feed_shadow.py compare\nshutoff: {"result": "failed"}\n!!! exit 1\n'
                   '=== 2026-11-10 21:40:00 EST python feed_shadow.py compare\nfeed shadow 2026-11-10: ...\n'
                   'shutoff: {"at": "x", "made": "created", "result": "unconfirmed"}\n!!! exit 1\n')
    text = ops_notify.build_message("feedshadow", log)
    assert "switch-off is UNCONFIRMED: the OFF file is in place but the running recorder did not acknowledge within 10 s." in text
    assert "no trading process was stopped" in text and "No action needed" not in text
    log.write_text(log.read_text() + '=== 2026-11-11 21:40:00 EST python feed_shadow.py compare\nshutoff: {"error": "x", "result": "failed"}\n!!! exit 1\n')
    assert "switch-off could NOT be made: the recorder is still on." in ops_notify.build_message("feedshadow", log)
    log.write_text(log.read_text() + '=== 2026-11-12 21:40:00 EST python feed_shadow.py compare\nTraceback\n!!! exit 1\n')
    assert "the feed shadow comparison did not finish" in ops_notify.build_message("feedshadow", log)   # another failure: as before
    assert "feed shadow" not in ops_notify.build_message("digest", log)


def test_the_job_and_the_check_write_only_their_own_files_and_never_remove_the_off_file():
    """It cannot change a flag, a size, a registered result or who is in a cohort: every database is opened
    read-only; the files written are the session's line, the evidence and the OFF file; the OFF file is never
    removed or written over by this code."""
    from sgt.fast import feed_shadow_check as check

    src, chk = Path(job.__file__).read_text(), Path(check.__file__).read_text()
    assert src.count("sqlite3.connect(") == 2 and src.count("?mode=ro\", uri=True") == 2
    assert chk.count("sqlite3.connect(") == 1 and chk.count("?mode=ro\", uri=True") == 1
    for text in (src, chk):
        assert "OpsStore" not in text and "set_flag" not in text and ".tx(" not in text and "fade_trades" not in text
    assert src.count("write_text(") == 1 and "tmp.replace(path)" in src and str(job.OUT) == "reports/feed_shadow/daily.jsonl"
    assert chk.count("write_text(") == 2 and chk.count("os.link(tmp, ") == 2 and ".replace(" not in chk.replace("str.replace", "")
    assert chk.count(".unlink(") == 2 and chk.count("tmp.unlink(missing_ok=True)") == 2             # only its temporary files


def test_a_sessions_regular_hours_lie_inside_one_utc_day_in_both_halves_of_the_year():
    """The cadence record is one file per UTC day (2026-11-27): no evaluation window is split across two files."""
    for day in ("2026-11-30", "2026-07-06", "2026-11-27"):
        first, close = fs.session_minutes(day)
        assert first.date() == close.date() == (close + pd.Timedelta(seconds=59)).date() == pd.Timestamp(day).date()


def test_the_nightly_reader_reads_the_old_file_and_the_days_own_file_as_one_record(tmp_path):
    """The day of the change: the morning's rows are in the old single file, the afternoon's in the day file."""
    from sgt.ops import own_thread

    rows_ = whole()
    old = gaps(tmp_path, [summary(-600, work=9.0)] + rows_[:15])                                      # an earlier day too
    own_thread.part(tmp_path, DAY).write_text("\n".join(json.dumps(r) for r in rows_[15:]) + "\n")
    own_thread.part(tmp_path, "2026-11-10").write_text(json.dumps(summary(60, work=9.0, day=OPEN + pd.Timedelta(days=1))) + "\n")
    o, kept = job._pace(DAY, old)
    assert (o["passes"], o["periods"], o["whole"], job.verdict(o["inside"]), len(kept)) == (1520, 38, True, "inside", 38)
    old.unlink()                                                                                     # only day files, later on
    assert job.pace(DAY, old)["periods"] == 23 and job.pace(DAY, old)["whole"] is False
    assert job.pace("2026-11-12", old)["note"] == "the cadence record could not be read (FileNotFoundError)"


def test_a_day_file_at_a_size_stop_is_not_measured_and_never_a_pass(tmp_path, monkeypatch):
    """Storage caps produce "not measured" where coverage is incomplete, never a false pass (owner 2026-11-27)."""
    from sgt.ops import own_thread

    own_thread.part(tmp_path, DAY).write_text("\n".join(json.dumps(r) for r in whole()) + "\n")
    size = own_thread.part(tmp_path, DAY).stat().st_size
    monkeypatch.setattr(own_thread, "GAPS_MAX_BYTES", size - 1)                                      # the first stop: late rows
    o = job.pace(DAY, tmp_path / own_thread.GAPS_NAME)
    assert o["inside"] == {"cadence": True, "late": None, "work_p90": True, "work_max": True} and o["whole"] is True
    assert "late-pass rows may be missing" in o["incomplete"] and job.verdict(o["inside"]) == "not measured: late"
    monkeypatch.setattr(own_thread, "GAPS_MAX_BYTES", size // 2 - 1)                                 # the second: every row
    o = job.pace(DAY, tmp_path / own_thread.GAPS_NAME)
    assert o["whole"] is False and set(o["inside"].values()) == {None} and o["longest_stretch_without_a_row_s"] < job.HOLE_S
    assert "the day's record reached the size at which no row is written any more" in o["incomplete"]
    rows_ = whole()
    rows_[7] = summary(80, work=0.7)                                                                 # an outside figure stays
    own_thread.part(tmp_path, DAY).write_text("\n".join(json.dumps(r) for r in rows_) + "\n")
    monkeypatch.setattr(own_thread, "GAPS_MAX_BYTES", 10)
    assert job.pace(DAY, tmp_path / own_thread.GAPS_NAME)["inside"] == {"cadence": None, "late": None, "work_p90": None, "work_max": False}


def test_a_vendor_request_that_fails_is_reported_and_the_symbol_is_left_out(tmp_path):
    run(tmp_path, [(at(0, 30) + pd.Timedelta(seconds=15 * k), s, frame([0])) for k in range(12) for s in ("ABCD", "EFGH")])
    fetch = lambda feed, symbols, a, b: ({"ABCD": {at(0): V}}, ["EFGH"] if feed == "sip" else [])   # noqa: E731
    o = job.compare(DAY, tmp_path / "shadow.db", tmp_path / "no-ops.db", fetch=fetch)
    assert o["symbols_failed"] == ["EFGH"] and o["symbols"] == 2 and o["watched_minutes"] > 0


def test_the_last_session_comes_from_the_exchange_calendar():
    assert job.last_session(datetime(2026, 11, 26, 21, 40, tzinfo=fs.ET)) == "2026-11-25"     # Thanksgiving: the day before
    assert job.last_session(datetime(2026, 11, 27, 21, 40, tzinfo=fs.ET)) == "2026-11-27"     # the early-close session
    assert job.last_session(datetime(2026, 11, 27, 12, 0, tzinfo=fs.ET)) == "2026-11-25"      # before that close
    assert fs.session_minutes("2026-11-27")[1] == pd.Timestamp("2026-11-27 18:00", tz="UTC")  # 13:00 ET


def test_the_listener_wires_the_tap_around_the_long_books_reader_only():
    src = (Path(__file__).resolve().parents[2] / "scripts" / "fade_live.py").read_text()
    assert src.count("feed_shadow.tap(") == 1 and "feed_shadow.tap(feed_shadow.start(), lambda sym, now: live_bars(lc, sym, now))" in src
    assert timedelta(minutes=1) == fs.BAR and datetime.now(timezone.utc).tzinfo is not None
