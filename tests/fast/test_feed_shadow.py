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


def test_the_tolerance_is_the_table_written_before_the_recorder_ran():
    """Pinned: these four numbers are section 4a's table of 2026-11-27 and are not moved after a result is seen."""
    assert job.TOL == {"within_min": 0.995, "late_max": 0, "work_p90_max_s": 0.2, "work_max_max_s": 0.5}
    assert (job.LOOP, str(job.PACE)) == ("long book", "reports/ops/loop_gaps.jsonl")


def test_the_long_books_own_cadence_record_is_read_for_the_sessions_regular_hours_only(tmp_path):
    work = [0.0, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.2, 0.4]
    p = gaps(tmp_path, [summary(10 + 10 * k, work=w) for k, w in enumerate(work)] + [
        summary(5, n=999, counts=(0, 0, 0, 999), work=9.0),                    # stamped before 09:40: before the window
        summary(391, n=999, counts=(0, 0, 0, 999), work=9.0),                  # stamped after the close
        summary(60, n=999, counts=(0, 0, 0, 999), work=9.0, loop="stop check"),   # another loop
        summary(60, n=999, counts=(0, 0, 0, 999), work=9.0, day=OPEN + pd.Timedelta(days=1)),   # another day
        {"at": (OPEN - pd.Timedelta(hours=2)).isoformat(), "loop": "long book", "gap": 61.0, "every": 15.0},  # late, pre-market
        "not json", json.dumps([1, 2]), json.dumps({"at": at(30).isoformat(), "loop": "long book", "kind": "summary", "n": "x"})])
    o = job.pace(DAY, p)
    assert (o["passes"], o["counts"], o["late"], o["periods"], o["unreadable_lines"]) == (400, [400, 0, 0, 0], 0, 10, 3)
    assert (o["within"], o["work_p90_s"], o["work_max_s"]) == (1.0, 0.2, 0.4)      # sorted[int(0.9 * 9)]: the baseline's rule
    assert o["window_et"] == "09:40-16:00" and o["full_session"] is True
    assert o["inside"] == {"cadence": True, "late": True, "work_p90": True, "work_max": True} and job.verdict(o["inside"]) == "inside"
    assert o["tolerance"] is job.TOL and "not proof of no effect" in o["limits"]


@pytest.mark.parametrize("rows_, outside", [
    ([summary(10, n=1000, counts=(995, 5, 0, 0), work=0.2), summary(20, n=0, counts=(0, 0, 0, 0), work=0.5)], []),   # at each limit
    ([summary(10, n=1000, counts=(994, 6, 0, 0), work=0.1)], ["cadence"]),
    ([summary(10, work=0.6)], ["work_p90", "work_max"]),
    ([summary(10, work=0.1), summary(20, work=0.6)], ["work_max"]),
    ([summary(10), {"at": at(45).isoformat(), "loop": "long book", "gap": 36.0, "every": 15.0}], ["late"]),
])
def test_a_figure_at_the_limit_is_inside_and_one_past_it_is_outside_by_name(tmp_path, rows_, outside):
    o = job.pace(DAY, gaps(tmp_path, rows_))
    assert [k for k, v in o["inside"].items() if v is False] == outside
    assert job.verdict(o["inside"]) == ("outside: " + ", ".join(outside) if outside else "inside")


def test_a_record_that_is_missing_or_empty_is_not_measured_and_never_a_pass(tmp_path):
    for o in (job.pace(DAY, tmp_path / "none.jsonl"), job.pace(DAY, gaps(tmp_path, [summary(60, loop="stop check")]))):
        assert o["note"] and set(o["inside"].values()) == {None}
        assert job.verdict(o["inside"]) == "not measured: cadence, late, work_p90, work_max"


def test_an_early_close_is_labelled_and_is_not_the_comparison_session(tmp_path):
    half = pd.Timestamp("2026-11-27 14:30", tz="UTC")
    o = job.pace("2026-11-27", gaps(tmp_path, [summary(30, day=half), summary(215, n=7, counts=(0, 0, 0, 7), day=half)]))
    assert (o["window_et"], o["full_session"], o["passes"]) == ("09:40-13:00", False, 40)     # 13:05 is after its close
    assert "(not a full session)" in job.pace_words({"pace": o, "longs_entered": 0})


def test_the_integrity_row_says_what_failed_and_what_was_not_measured():
    good = {"health": {"dropped": 0, "fails": 0}, "watched_minutes": 5, **{k: 1 for k in fs.CLASSES}}
    assert job.verdict(job.integrity(good)) == "inside"
    assert job.verdict(job.integrity({**good, "health": {"dropped": 2, "fails": 0, "off": {"why": "x"}}})) == "outside: dropped_0, not_switched_off"
    assert job.verdict(job.integrity({**good, "delivered": 3})) == "outside: counts_add_up"
    assert job.verdict(job.integrity({"health": {}})) == "not measured: dropped_0, write_fails_0, not_switched_off, counts_add_up"


def test_the_nightly_line_carries_the_check_with_each_figure_beside_its_limit(tmp_path):
    looks = [(at(0, 30) + pd.Timedelta(seconds=15 * k), "ABCD", frame([0] if k > 6 else [])) for k in range(0, 20)]
    run(tmp_path, looks)
    px = (10.0, 10.1, 9.9, 10.0, 100.0)
    p = gaps(tmp_path, [summary(10, work=0.1), summary(20, work=0.3)])
    o = job.compare(DAY, tmp_path / "shadow.db", tmp_path / "no-ops.db", fetch=lambda *a: ({"ABCD": {at(0): px}}, []), gaps=p)
    assert o["pace"]["passes"] == 80 and o["longs_entered"] == 0 and job.verdict(o["integrity"]) == "inside"
    said = job.words(o)
    assert ("frozen check, long book 09:40-16:00 ET: 80 passes, 100.00% started within 1.1 x 15 s (limit at least 99.5%), "
            "late 0 (limit 0), longest pass per 10 minutes: 90th percentile 0.1 s (limit 0.2), maximum 0.3 s (limit 0.5), "
            "0 longs entered; timing inside; integrity inside") in said
    json.dumps(o)                                                        # the line is written as JSON
    empty = job.compare("2026-11-10", tmp_path / "shadow.db", tmp_path / "no-ops.db", fetch=None, gaps=tmp_path / "none.jsonl")
    assert "no look was recorded" in job.words(empty) and "timing not measured" in job.words(empty)


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
