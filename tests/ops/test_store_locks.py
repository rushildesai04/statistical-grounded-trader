"""The ops database's write lock (measured 2026-11-22, docs/findings/ops_risks_2026-11-22.md): a read must not hold
it, and a wait for it is written down. A held lock delays every writer behind it, the stop check included."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from sgt.fast import est_costs, fade
from sgt.ops import approvals as ap
from sgt.ops import decision_health, journal, store
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")
T10 = datetime(2026, 11, 2, 10, 0, tzinfo=ET).astimezone(timezone.utc)


@pytest.fixture()
def db(tmp_path):
    d = OpsStore(tmp_path / "ops.db")
    ap.arm(d, T10)
    return d


def open_short(db):
    fade.on_alert(db, fade.LiveAlert("ABCD", "volume_spike", T10 - timedelta(seconds=10), "-1", 1, None), now=T10)
    fade.monitor(db, [].append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=20))


def test_a_read_takes_no_write_lock_and_a_write_does(db):
    with db.read() as c:
        c.execute("SELECT COUNT(*) FROM fade_trades").fetchone()
        t = time.monotonic()
        db.set_flag("k", "v")                                       # a trading write while the read is still open
        assert time.monotonic() - t < 0.5
    assert db.flag("k") == "v"
    with db.tx():                                                   # the control: a write block does hold it
        other = sqlite3.connect(db.path, timeout=0.2, isolation_level=None)
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            other.execute("BEGIN IMMEDIATE")
        other.close()


def test_a_read_cannot_write(db):
    with db.read() as c, pytest.raises(sqlite3.OperationalError):
        c.execute("INSERT INTO flags(key, value, updated_at) VALUES ('a','b','c')")
    assert db.flag("a") is None


def test_the_notes_page_being_built_does_not_delay_a_stop_exit(db, monkeypatch):
    open_short(db)
    inside, release = threading.Event(), threading.Event()

    def slow_cost(*a, **k):                                         # the page is in the middle of its build
        inside.set()
        release.wait(5)
        return 0.01

    monkeypatch.setattr(est_costs, "cost", slow_cost)
    page = threading.Thread(target=journal.entries, args=(db,))
    page.start()
    assert inside.wait(5)
    monkeypatch.setattr(est_costs, "cost", lambda *a, **k: 0.01)
    sent, t = [], time.monotonic()
    fade.monitor(db, sent.append, lambda s, **k: 4.9, now=T10 + timedelta(minutes=6))             # through the 20% stop
    took = time.monotonic() - t
    release.set()
    page.join(5)
    with db.tx() as c:
        assert c.execute("SELECT status, reason FROM fade_trades").fetchone()[:] == ("est_closed", "stop")
    assert sent and took < 1.0                                      # under tx() this pass waited for the whole page


def test_a_slow_hold_and_the_wait_behind_it_are_written_down_with_who(db, monkeypatch):
    monkeypatch.setattr(store, "SLOW_S", 0.05)
    started = threading.Event()

    def holder_of_the_lock():
        with db.tx():
            started.set()
            time.sleep(0.2)

    th = threading.Thread(target=holder_of_the_lock)
    th.start()
    assert started.wait(5)
    db.set_flag("k", "v")                                           # waits behind the holder
    th.join(5)
    day = datetime.now(timezone.utc).date().isoformat()
    rows = db.slow(day)
    hold = next(r for r in rows if r["hold"] >= 0.15)
    wait = next(r for r in rows if r["wait"] >= 0.05)
    assert "holder_of_the_lock" in hold["who"] and "set_flag" in wait["who"] and not wait["failed"]
    assert all(json.dumps(r) for r in rows)
    got = decision_health.database(db, day)
    assert got.startswith(f"database: {len(rows)} slow, longest wait 0.") and "holder_of_the_lock" in got
    assert decision_health.database(db, "2020-01-01") == "database: no write waited 0.25 s"


def test_a_wait_of_a_second_is_a_warning(db):
    day = "2026-11-02"
    db.spool_path.with_name(store.WAITS_NAME).write_text(
        json.dumps({"at": f"{day}T15:00:00+00:00", "wait": 2.1, "hold": 0.0, "who": "fade.py:monitor:310"}) + "\n"
        + json.dumps({"at": f"{day}T15:00:00+00:00", "wait": 0.0, "hold": 3.0, "who": "journal.py:entries:48"}) + "\n")
    assert decision_health.database(db, day) == ("⚠️ database: 2 slow, longest wait 2.1 s (fade.py:monitor), "
                                                 "longest hold 3.0 s (journal.py:entries)")


def test_writing_the_measurement_down_can_never_break_the_write(db, monkeypatch):
    monkeypatch.setattr(store, "SLOW_S", 0.0)                       # every block is "slow"
    monkeypatch.setattr(store, "WAITS_NAME", "no/such/dir/x.jsonl")
    db.set_flag("k", "v")
    assert db.flag("k") == "v"


def test_the_lists_the_trading_loops_read_do_not_wait_for_a_writer(db):
    """Lock record of 2026-11-24: ``alert_context.due`` waited 1.3 s for the write lock, ``tape_check._rows`` held it
    0.37 s with the long book's exit check (0.43 s) behind it, ``scanner.step`` waited 1.0 s. Each of them only reads
    its list there. With another process in the middle of a write, each returns at once."""
    import pandas as pd

    from sgt.fast import alert_context, long_book, scanner, tape_check

    now = T10 + timedelta(minutes=1)
    open_short(db)
    db.set_flag("k", "v")

    def while_writing(call):
        """``call()`` with the write lock held by someone else: (seconds it took, what it returned)."""
        got, release = [], threading.Event()

        def writer():
            with db.tx() as c:
                c.execute("UPDATE flags SET value='x' WHERE key='k'")
                got.append("locked")
                release.wait(3)

        w = threading.Thread(target=writer)
        w.start()
        while not got:
            time.sleep(0.01)
        try:
            t0 = time.monotonic()
            out = call()
            return time.monotonic() - t0, out
        finally:
            release.set()
            w.join()

    took = {
        "alert_context.due": while_writing(lambda: alert_context.due(db, now)),
        "tape_check._rows": while_writing(lambda: tape_check._rows(db, now)),
        "long_book._advance": while_writing(lambda: long_book._advance(db, lambda s, t: pd.DataFrame(), [].append, now)),
        "scanner.step": while_writing(lambda: scanner.step(db, lambda s, **k: None, now)),
    }
    assert {k: round(v[0], 1) for k, v in took.items() if v[0] >= 0.5} == {}
    assert [r["symbol"] for r in took["alert_context.due"][1]] == ["ABCD"]     # and they still read what is there
    assert [r["symbol"] for r in took["tape_check._rows"][1]] == ["ABCD"]
