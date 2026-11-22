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
