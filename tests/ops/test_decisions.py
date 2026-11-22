"""The decision record (audit 2026-11-22): what each book decided, when, on what facts; never at the cost of a trade."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from sgt.fast import fade
from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")
T10 = datetime(2026, 11, 2, 10, 0, tzinfo=ET).astimezone(timezone.utc)


@pytest.fixture()
def db(tmp_path):
    d = OpsStore(tmp_path / "ops.db")
    ap.arm(d, T10)
    return d


def alert(sym="ABCD", kind="volume_spike", mid=1, at=T10 - timedelta(seconds=10)):
    return fade.LiveAlert(sym, kind, at, "-1", mid, True if kind == "news" else None)


def log(db, book="short"):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT * FROM decisions WHERE book=? ORDER BY id", (book,))]


def test_a_short_leaves_one_line_per_decision_with_the_facts_of_that_moment(db):
    fade.on_alert(db, alert(), now=T10)
    fade.monitor(db, [].append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=20))
    fade.monitor(db, [].append, lambda s, **k: 4.1, now=T10 + timedelta(minutes=5))              # a price mark only
    fade.monitor(db, [].append, lambda s, **k: 4.9, now=T10 + timedelta(minutes=6))              # through the 20% stop
    rows = log(db)
    assert [(r["state_from"], r["state_to"], r["reason"]) for r in rows] == [
        (None, "pending", None), ("pending", "open", None), ("open", "est_closed", "stop")]
    first, opened, closed = (json.loads(r["facts"]) for r in rows)
    assert first["alert_type"] == "volume_spike" and first["received_after_s"] == 10.0 and first["posted_at"] < first["received_at"]
    assert opened["est_entry"] == 4.0 and "est_entry_at" in opened and closed["est_exit"] == 4.9      # booked at the print that crossed, never better than the level
    assert all(r["config"] == fade.RULE_VERSION and r["ref"] == rows[0]["ref"] for r in rows)
    assert db.decisions("short", rows[0]["ref"]) == rows


def test_a_blocked_alert_and_a_duplicate_are_on_the_record_with_their_reason(db):
    fade.on_alert(db, alert("NEWS", "news", 1), now=T10)                                          # the news rule
    fade.on_alert(db, alert("ABCD", mid=2), now=T10)
    fade.on_alert(db, alert("ABCD", "new_high", 3), now=T10 + timedelta(seconds=5))               # same stock again
    got = [(r["symbol"], r["state_to"], r["reason"]) for r in log(db)]
    assert got == [("NEWS", "filtered", fade.NEWS_REASON), ("ABCD", "pending", None),
                   ("ABCD", "duplicate", "the stock already has a live alert today")]
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM fade_trades").fetchone()[0] == 2                   # no row for the duplicate


def test_a_gate_at_the_entry_is_recorded_with_what_it_read(db):
    fade.on_alert(db, alert(), now=T10)
    fade.monitor(db, [].append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=20), beta=lambda sym, at: 3.4)
    last = log(db)[-1]
    assert (last["state_from"], last["state_to"], last["reason"]) == ("pending", "filtered", fade.BETA_REASON)
    assert json.loads(last["facts"]) == {"beta": 3.4}


def test_the_record_can_never_cost_a_trade(db):
    with db.tx() as c:
        c.execute("DROP TABLE decisions")                                                          # the record is broken
    assert fade.on_alert(db, alert(), now=T10).startswith("pending")
    fade.monitor(db, [].append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=20))
    with db.tx() as c:
        assert c.execute("SELECT status FROM fade_trades").fetchone()[0] == "open"
    db.decide("short", 1, "X", None, "y", facts_that_do_not_serialise=object())                    # never raises


def test_facts_and_reasons_are_bounded(db):
    db.decide("long", 7, "ZZZ", "open", "est_closed", "r" * 5000, "cfg", note="n" * 9000, none=None)
    (r,) = log(db, "long")
    assert len(r["reason"]) == 300 and len(r["facts"]) <= 2000 and "none" not in r["facts"]


# --- failure behaviour (audit 2026-11-22, phase 3b): never block, never raise, never silent -------------------------

import logging  # noqa: E402
import sqlite3  # noqa: E402
import time  # noqa: E402

from sgt.ops import decision_health, store  # noqa: E402


def count(db):
    with db.tx() as c:
        return c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]


def test_a_locked_database_costs_the_trading_loop_half_a_second_and_no_line(db, caplog):
    held = sqlite3.connect(db.path, timeout=1, isolation_level=None)
    held.execute("BEGIN IMMEDIATE")                                  # another process is writing
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING):
        db.decide("short", 7, "ABCD", "open", "est_closed", "stop", "cfg", est_exit=4.9)
    waited = time.monotonic() - t0
    held.execute("ROLLBACK")
    held.close()
    assert waited < store.DECIDE_TIMEOUT_S + 1.0                     # bounded: the 30 s wait of other writes is not used
    assert db.spooled() == 1 and "kept in the spool" in caplog.text  # not silent, not lost
    assert count(db) == 0
    assert db.decide_replay() == 1 and db.spooled() == 0
    r = db.decisions("short", 7)[0]
    assert (r["state_to"], r["reason"], json.loads(r["facts"])) == ("est_closed", "stop", {"est_exit": 4.9})
    assert db.decide_replay() == 0 and count(db) == 1                # nothing left, nothing doubled


def test_a_missing_table_is_spooled_and_the_replay_rebuilds_it(db):
    with db.tx() as c:
        c.execute("DROP TABLE decisions")
    db.decide("long", 3, "WXYZ", None, "open", None, "cfg", stop=1.0)
    assert db.spooled() == 1
    got = decision_health.line(db, "2026-11-02")                     # the nightly check replays first
    assert "cannot be read" not in got and "1 late lines put back" in got
    assert db.spooled() == 0 and [r["ref"] for r in db.decisions("long", 3)] == [3]


def test_a_replay_that_died_after_its_commit_does_not_double_a_line(db):
    held = sqlite3.connect(db.path, timeout=1, isolation_level=None)
    held.execute("BEGIN IMMEDIATE")
    db.decide("short", 1, "ABCD", None, "pending")
    held.execute("ROLLBACK")
    held.close()
    kept = db.spool_path.read_text()
    assert db.decide_replay() == 1
    db.spool_path.with_suffix(".replaying").write_text(kept)         # the unlink never happened
    assert db.decide_replay() == 0 and count(db) == 1 and db.spooled() == 0


def test_a_dead_disk_loses_the_line_loudly_and_still_does_not_raise(db, monkeypatch, caplog):
    def no_db(self, lines, timeout):
        raise sqlite3.OperationalError("disk I/O error")

    def no_file(self, line, err, real=OpsStore._decide_spool):
        monkeypatch.setattr(OpsStore, "spool_path", property(lambda s: s.path.parent / "no" / "such" / "dir" / "x"))
        return real(self, line, err)

    monkeypatch.setattr(OpsStore, "_decide_write", no_db)
    monkeypatch.setattr(OpsStore, "_decide_spool", no_file)
    with caplog.at_level(logging.ERROR):
        db.decide("short", 9, "ABCD", "open", "est_closed", "stop")
    assert db.decide_lost == 1 and "DECISION RECORD LINE LOST" in caplog.text


def test_a_trade_is_booked_even_when_the_record_cannot_be_written_at_all(db, monkeypatch):
    def boom(self, *a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(OpsStore, "_decide_write", boom)
    fade.on_alert(db, alert(), now=T10)
    fade.monitor(db, [].append, lambda s, **k: 4.0, now=T10 + timedelta(seconds=20))
    sent = []
    fade.monitor(db, sent.append, lambda s, **k: 4.9, now=T10 + timedelta(minutes=6))              # the protective exit
    with db.tx() as c:
        assert c.execute("SELECT status, reason FROM fade_trades").fetchone()[:] == ("est_closed", "stop")
    assert sent and db.spooled() == 3                                # the exit text went out; three lines wait


def alerts_db(tmp_path, rows):
    p = tmp_path / "sgt.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE crowd_alerts(symbol TEXT, alert_type TEXT, posted_at TEXT, has_news INTEGER)")
    c.executemany("INSERT INTO crowd_alerts VALUES (?,?,?,?)", rows)
    c.commit()
    c.close()
    return p


def test_the_nightly_line_says_complete_only_when_every_row_and_every_channel_alert_has_a_line(db, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "utcnow", lambda: T10 - timedelta(days=3))
    db.decide("short", None, "OLDR", None, "duplicate")              # the record began on an earlier day
    monkeypatch.setattr(store, "utcnow", lambda: T10)                # a line is stamped with the clock, as live
    at = T10 - timedelta(seconds=10)
    fade.on_alert(db, alert(), now=T10)
    fade.on_alert(db, alert("ABCD", "new_high", 2, at + timedelta(seconds=5)), now=T10 + timedelta(seconds=5))   # duplicate
    stamp = lambda t: t.strftime("%Y-%m-%d %H:%M:%S.000000")  # noqa: E731 - the channel table's format
    chan = [("ABCD", "volume_spike", stamp(at), None), ("ABCD", "new_high", stamp(at + timedelta(seconds=5)), None),
            ("ABCD", "halted", stamp(at), None), ("NEWS", "news", stamp(at), 0)]                  # the last two: not tested kinds
    ok = decision_health.line(db, "2026-11-02", alerts_db(tmp_path, chan))
    assert ok.startswith("decision record: complete (1 of 1 alert rows, 2 lines; 2 channel alerts")
    (tmp_path / "sgt.db").unlink()
    gap = decision_health.line(db, "2026-11-02", alerts_db(tmp_path, chan + [("VRAX", "volume_increasing", stamp(at), None)]))
    assert gap.startswith("⚠️ decision record INCOMPLETE: 1 channel alerts never reached the book (UTC: VRAX 14:59:50)")


def test_a_row_without_a_line_and_a_waiting_spool_are_both_named(db, monkeypatch, tmp_path):
    monkeypatch.setattr(store, "utcnow", lambda: T10)
    fade.on_alert(db, alert(), now=T10)
    with db.tx() as c:                                               # a row written by a path that left no line
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                  "created_at) VALUES ('QQQQ','2026-11-02','new_high',?, '-1', 99, 'pending', ?)",
                  (T10.isoformat(), (T10 + timedelta(seconds=1)).isoformat()))
    monkeypatch.setattr(OpsStore, "decide_replay", lambda self: 0)   # the database stays busy all night
    db.spool_path.write_text(json.dumps({"line": ["x"] * 9}) + "\n")
    got = decision_health.line(db, "2026-11-02", tmp_path / "absent.db")
    assert got == ("⚠️ decision record INCOMPLETE: 1 of 2 alert rows have no line; 1 lines waiting in the spool; "
                   "database: no write waited 0.25 s")
