"""Listener recovery (owner 2026-11-22): an alert posted while the listener was down is found, labelled and recorded,
handled once whatever is delivered twice, in the channel's order, and never traded on an old price or after its
window. A "restart" here is a new ``Listener``: its own ``OpsStore`` on the same file and fresh in-memory state."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from sgt.fast import alert_recovery as ar
from sgt.fast import fade, long_book as lb
from sgt.fast import patterns as pt
from sgt.ops import alert_log, approvals as ap, decision_health
from sgt.ops.store import OpsStore, iso

ET = ZoneInfo("America/New_York")
CH = "-1"


def et(h, m, s=0, day=2):
    return datetime(2026, 11, day, h, m, s, tzinfo=ET).astimezone(timezone.utc)      # 2026-11-02: a Monday session


POSTED = et(10, 0, 20)                      # the tested entry bar starts 10:01:00


def to_alerts(text, posted, mid):
    """'AAA:volume_spike,BBB:new_high' -> one alert per item ('news' = a news alert with company news)."""
    if text == "BAD":
        raise ValueError("not an alert message")
    out = []
    for item in filter(None, text.split(",")):
        sym, kind = item.split(":")
        out.append(fade.LiveAlert(sym, kind, posted, CH, mid, True if kind == "news" else None))
    return out


class Channel:
    def __init__(self):
        self.msgs, self.calls, self.fail = {}, [], 0

    def post(self, mid, text, at):
        self.msgs[mid] = SimpleNamespace(id=mid, message=text, date=at)
        return self.msgs[mid]

    async def fetch(self, after, limit):
        self.calls.append((after, limit))
        if self.fail:
            self.fail -= 1
            raise OSError("channel unreachable")
        return [self.msgs[i] for i in sorted(self.msgs) if i > after][:limit]

    async def newest(self):
        return max(self.msgs, default=0)

    async def before(self, t):
        return max((i for i, m in self.msgs.items() if m.date < t), default=None)


class Listener:
    def __init__(self, path, ch, up):
        self.db, self.ch, self.state, self.clock = OpsStore(path), ch, ar.State(CH, up), up
        self.order, self.on_alert = [], fade.on_alert

    def handle(self, m, how):
        self.order.append((m.id, how))
        return ar.dispatch(self.db, m, how, to_alerts, on_alert=lambda *a: self.on_alert(*a), now=lambda: self.clock)

    def catch_up(self, at=None):
        self.clock = at or self.clock
        return asyncio.run(ar.catch_up(self.db, self.state, self.ch.fetch, self.handle, self.ch.newest, self.ch.before,
                                       now=lambda: self.clock))

    def push(self, m, at=None):
        self.clock = at or self.clock
        return ar.pushed(self.state, self.handle)(m)


class Tape:
    """A quote that knows when its print was made (as the live one does)."""
    since_aware = True

    def __init__(self, px=None, at=None):
        self.px, self.at = px, at

    def __call__(self, symbol, since=None):
        return None if self.px is None or (since is not None and self.at < since) else self.px


@pytest.fixture()
def path(tmp_path):
    p = tmp_path / "ops.db"
    ap.arm(OpsStore(p), et(9, 0))
    return p


@pytest.fixture()
def ch():
    c = Channel()
    c.post(9, "OLD:halted", et(9, 40))
    return c


def started(path, ch, up=et(9, 45)):
    """A listener that has been running (cursor set at the channel's newest message, push open)."""
    li = Listener(path, ch, up)
    assert li.catch_up().done and li.state.caught_up
    return li


def q(db, sql, *args):
    with db.read() as c:
        return [dict(r) for r in c.execute(sql, args)]


def rows(db):
    return q(db, "SELECT * FROM fade_trades ORDER BY id")


def events(db):
    return q(db, "SELECT * FROM alert_events ORDER BY message_id, symbol")


def cursor(db):
    return alert_log.cursor(db, CH)


def gaps(db):
    """The recorded gaps, without the one a book with no record at all writes on its very first start."""
    return [g for g in alert_log.gaps(db, "2000") if g["reason"] != ar.FIRST_START]


# ---- the durable identifiers and the cursor ---------------------------------------------------------------------

def test_a_book_with_no_record_starts_at_the_newest_message_and_writes_down_what_it_did_not_read(path, ch):
    ch.post(10, "AAA:volume_spike", et(9, 41))
    li = Listener(path, ch, et(9, 45))
    out = li.catch_up()
    assert (out.done, out.handled, li.order) == (True, 0, []) and cursor(li.db) == 10 and events(li.db) == []
    (g,) = alert_log.gaps(li.db, "2000")                    # the interval it chose not to read is on the record
    assert (g["from_id"], g["to_id"], g["reason"]) == (None, 10, ar.FIRST_START)


def test_the_first_start_resumes_after_the_last_message_on_record_so_nothing_posted_meanwhile_is_dropped(path, ch):
    """Review 2026-11-23: #200 set a missing cursor at the channel's NEWEST message. An alert posted during the very
    restart that first ran the code (or pushed while the cursor was being set) was then never read, and nothing said
    so. The anchor is now the last message the book has any record of; what came after it is replayed."""
    db = OpsStore(path)
    with db.tx() as c:                                      # handled by the build before identifiers: a row, no cursor
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                  "created_at) VALUES ('OLD','2026-11-02','volume_spike',?,?,10,'skipped','paused',?)",
                  (iso(et(9, 50)), CH, iso(et(9, 50))))
    ch.post(10, "OLD:volume_spike", et(9, 50))
    ch.post(11, "AAA:volume_spike", POSTED)                 # posted while the new build was starting
    li = Listener(path, ch, et(10, 0, 40))
    live = ch.post(12, "BBB:volume_spike", et(10, 0, 41))
    assert not li.push(live, et(10, 0, 42))                 # pushed while the cursor is being set: waits its turn
    out = li.catch_up(et(10, 0, 45))
    assert out.done and cursor(li.db) == 12 and li.order == [(11, "replay"), (12, "poll")]
    assert [(r["symbol"], r["status"], r["via"]) for r in rows(li.db)] == [
        ("OLD", "skipped", None), ("AAA", "pending", "replay"), ("BBB", "pending", "poll")]
    assert alert_log.gaps(li.db, "2000") == []              # nothing was passed, so nothing to write down


def test_an_alert_whose_row_was_written_by_a_build_without_identifiers_never_gets_a_second_row(path):
    """Rolling back to the build before identifiers and forward again (or the first start after the change): the
    older build's rows have no identifier, and its cursor did not move. The replay must recognise them."""
    db = OpsStore(path)
    posted = et(9, 0)                                       # outside the entry window: a FILTERED row, which the
    with db.tx() as c:                                      # one-row-per-stock index does not protect
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                  "created_at) VALUES ('AAA','2026-11-02','volume_spike',?,?,20,'filtered','outside 09:30-15:44',?)",
                  (iso(posted), CH, iso(posted)))
    a = fade.LiveAlert("AAA", "volume_spike", posted, CH, 20, via="replay", recovered_at=et(9, 30))
    assert fade.on_alert(db, a, et(9, 30)) == "already handled"
    assert fade.on_alert(db, a, et(9, 31)) == "already handled (filtered)"          # and from then on by its identifier
    assert len(rows(db)) == 1 and q(db, "SELECT 1 FROM decisions") == []
    (e,) = events(db)
    assert (e["status"], e["fade_id"], e["via"], e["reason"]) == ("filtered", 1, None, alert_log.LEGACY)
    assert ar.account(db, "2026-11-02", "2026-11-02")["recovered"] == 0             # not counted as recovered


def test_the_cursor_moves_only_when_every_alert_of_the_message_is_on_record_and_never_back(path):
    db = OpsStore(path)
    a, b = (CH, 12, "AAA", "volume_spike"), (CH, 12, "BBB", "new_high")
    alert_log.baseline(db, CH, 11)
    alert_log.note(db, a, POSTED, POSTED, "poll", None, "none", "test")
    assert not alert_log.advance(db, CH, 12, [a, b]) and cursor(db) == 11          # BBB is not on record
    alert_log.note(db, b, POSTED, POSTED, "poll", None, "none", "test")
    assert alert_log.advance(db, CH, 12, [a, b]) and cursor(db) == 12
    assert alert_log.advance(db, CH, 5, []) and cursor(db) == 12                   # never backwards
    assert not alert_log.baseline(db, CH, 3) and cursor(db) == 12


def test_an_unreadable_cursor_is_never_read_as_zero(path, ch):
    db = OpsStore(path)
    db.set_flag(alert_log.CURSOR + CH, "garbage")
    assert cursor(db) is None
    db.set_flag(alert_log.CURSOR + CH, "0")
    assert cursor(db) is None                               # "everything after 0" would be the channel's whole history
    li = Listener(path, ch, et(9, 45))
    assert li.catch_up().done and cursor(li.db) == 9 and li.order == []
    (g,) = alert_log.gaps(li.db, "2000")
    assert g["reason"] == ar.BAD_CURSOR and g["to_id"] == 9  # restarted from the newest message, and written down


def test_an_unreadable_cursor_with_alerts_on_record_resumes_after_the_last_of_them(path, ch):
    a = started(path, ch)
    a.push(ch.post(10, "AAA:volume_spike", POSTED), POSTED + timedelta(seconds=10))
    a.catch_up(POSTED + timedelta(seconds=15))
    a.db.set_flag(alert_log.CURSOR + CH, "garbage")
    ch.post(11, "BBB:volume_spike", POSTED + timedelta(seconds=30))
    b = Listener(path, ch, et(10, 2))
    assert b.catch_up(et(10, 2, 5)).done and cursor(b.db) == 11 and b.order == [(11, "replay")]
    assert [g["reason"] for g in gaps(b.db)] == [ar.BAD_CURSOR_ANCHORED]             # written down; nothing was passed


def test_one_alert_is_handled_once_whatever_is_delivered_twice(path):
    db = OpsStore(path)
    a = fade.LiveAlert("AAA", "volume_spike", POSTED, CH, 20)
    assert fade.on_alert(db, a, POSTED + timedelta(seconds=15)).startswith("pending #")
    assert fade.on_alert(db, a, POSTED + timedelta(seconds=40)) == "already handled (pending)"
    late = fade.LiveAlert("BBB", "volume_spike", et(9, 0), CH, 21)                  # outside the window: a filtered row
    assert fade.on_alert(db, late, et(9, 0, 5)) == "filtered: window"
    assert fade.on_alert(db, late, et(9, 0, 9)) == "already handled (filtered)"     # before: a second filtered row
    other = fade.LiveAlert("ZZZ", "halted", POSTED, CH, 22)
    assert fade.on_alert(db, other, POSTED) == "not a cell" and fade.on_alert(db, other, POSTED).startswith("already")
    assert [(r["symbol"], r["status"]) for r in rows(db)] == [("AAA", "pending"), ("BBB", "filtered")]
    assert [(e["symbol"], e["status"], e["fade_id"]) for e in events(db)] == [
        ("AAA", "pending", 1), ("BBB", "filtered", 2), ("ZZZ", "none", None)]
    assert len(q(db, "SELECT 1 FROM decisions WHERE book='short'")) == 2            # one line per row, not per delivery


def test_two_writers_racing_past_the_first_check_still_leave_one_row(path):
    """``on_alert`` looks the alert up before it classifies it. Should two deliveries both get past that look, the
    write itself decides: the second finds the identifier inside its own transaction and writes nothing; and under
    that, the table's key refuses a second record whatever the code above does."""
    db = OpsStore(path)
    a = fade.LiveAlert("AAA", "volume_spike", POSTED, CH, 20)
    now = POSTED + timedelta(seconds=15)
    assert fade._insert(db, a, "2026-11-02", "pending", "", now) == 1
    assert fade._insert(db, a, "2026-11-02", "pending", "", now) == fade.ALREADY
    assert fade._insert(db, a, "2026-11-02", "filtered", "outside 09:30-15:44", now) == fade.ALREADY
    assert len(rows(db)) == len(events(db)) == 1 and len(q(db, "SELECT 1 FROM decisions")) == 1
    with pytest.raises(sqlite3.IntegrityError), db.tx() as c:
        alert_log.put(c, alert_log.key(a), POSTED, now, "poll", None, "pending", None, 99)
    assert not alert_log.note(db, alert_log.key(a), POSTED, now, "poll", None, "none", "again")


def test_the_row_and_its_identifier_are_stored_together_or_not_at_all(path, monkeypatch):
    db = OpsStore(path)
    a = fade.LiveAlert("AAA", "volume_spike", POSTED, CH, 20)
    real = alert_log.put

    def dies(*args, **kw):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(alert_log, "put", dies)             # the process dies between the two writes
    with pytest.raises(sqlite3.OperationalError):
        fade.on_alert(db, a, POSTED + timedelta(seconds=15))
    assert rows(db) == [] and events(db) == []              # no row without its identifier
    monkeypatch.setattr(alert_log, "put", real)
    assert fade.on_alert(db, a, POSTED + timedelta(seconds=20)) == "pending #1" and len(rows(db)) == len(events(db)) == 1


# ---- the replay ---------------------------------------------------------------------------------------------------

def test_an_alert_posted_while_the_listener_was_down_is_recorded_with_both_times_and_a_replay_label(path, ch):
    started(path, ch)                                       # runs, then dies
    ch.post(10, "AAA:volume_spike", POSTED)
    b = Listener(path, ch, et(10, 2))                       # back at 10:02:00
    out = b.catch_up(et(10, 2, 5))
    assert (out.handled, out.recovered, out.done) == (1, 1, True) and b.order == [(10, "replay")]
    (r,), (e,) = rows(b.db), events(b.db)
    for rec in (r, e):
        assert rec["via"] == "replay" and rec["posted_at"] == iso(POSTED) and rec["recovered_at"] == iso(et(10, 2, 5))
    (d,) = q(b.db, "SELECT facts FROM decisions WHERE book='short' AND ref=?", r["id"])
    f = json.loads(d["facts"])
    assert (f["via"], f["recovered_at"], f["recovered_age_s"], f["posted_at"]) == (
        "replay", iso(et(10, 2, 5)), 105.0, iso(POSTED))    # the decision record keeps both timelines
    ch.post(11, "BBB:volume_spike", et(10, 2, 30))          # posted after it came back: not a replay
    b.catch_up(et(10, 2, 40))
    assert rows(b.db)[-1]["via"] == "poll" and rows(b.db)[-1]["recovered_at"] is None


def test_recovery_is_not_eligibility_a_stale_recovered_alert_is_recorded_and_never_entered(path, ch):
    started(path, ch)
    ch.post(10, "AAA:volume_spike", POSTED)
    b = Listener(path, ch, et(10, 6, 25))
    b.catch_up(et(10, 6, 30))                               # 6 min 10 s after the post
    (r,) = rows(b.db)
    assert (r["status"], r["reason"], r["via"]) == ("skipped", "alert received late", "replay")
    sent = []
    fade.monitor(b.db, sent.append, Tape(5.0, et(10, 6, 40)), now=et(10, 6, 45), up_since=b.state.up_since)
    assert rows(b.db)[0]["est_entry"] is None and rows(b.db)[0]["status"] == "skipped" and sent == []
    assert fade.prior_alert(b.db, "AAA", et(10, 0, day=3).astimezone(ET).date()) == "2026-11-02"   # still an event (F8)


def test_a_fresh_recovered_alert_enters_only_on_a_print_after_the_recovery_never_on_an_old_price(path, ch):
    started(path, ch)
    ch.post(10, "AAA:volume_spike", POSTED)
    b = Listener(path, ch, et(10, 2))
    b.catch_up(et(10, 2, 5))
    assert rows(b.db)[0]["status"] == "pending"             # 105 s old: the existing rules still allow an entry
    tape, sent = Tape(5.00, et(10, 1, 30)), []              # the latest print is from BEFORE the recovery
    fade.monitor(b.db, sent.append, tape, now=et(10, 2, 10), up_since=b.state.up_since)
    assert rows(b.db)[0]["status"] == "pending" and sent == []          # 5.00 is history: not an entry
    tape.px, tape.at = 5.40, et(10, 2, 20)                  # the first print after the recovery
    fade.monitor(b.db, sent.append, tape, now=et(10, 2, 25), up_since=b.state.up_since)
    r = rows(b.db)[0]
    assert (r["status"], r["est_entry"], r["est_entry_at"]) == ("open", 5.40, iso(et(10, 2, 25))) and len(sent) == 1
    assert r["posted_at"] == iso(POSTED) and r["recovered_at"] == iso(et(10, 2, 5))    # intended and actual timelines


def test_no_print_after_the_recovery_inside_the_window_means_no_trade_and_it_stays_an_event(path, ch):
    started(path, ch)
    ch.post(10, "AAA:volume_spike,HLT:volume_spike", POSTED)
    b = Listener(path, ch, et(10, 2))
    b.catch_up(et(10, 2, 5))
    quotes = {"AAA": Tape(5.00, et(10, 1, 30)), "HLT": Tape(3.00, et(9, 59))}       # HLT never printed after the alert
    quote = lambda s, since=None: quotes[s](s, since)  # noqa: E731
    quote.since_aware = True
    fade.monitor(b.db, lambda t: None, quote, now=et(10, 5, 30), up_since=b.state.up_since)
    got = {r["symbol"]: (r["status"], r["reason"], r["est_entry"]) for r in rows(b.db)}
    assert got == {"AAA": ("skipped", fade.RESTART_NO_PRINT, None), "HLT": ("skipped", fade.NO_PRINT_REASON, None)}
    assert fade.RESTART_NO_PRINT not in fade.NOT_EVENT_SKIPS and fade.NO_PRINT_REASON in fade.NOT_EVENT_SKIPS


def test_a_quote_that_cannot_prove_its_time_never_enters_a_recovered_alert(path, ch):
    started(path, ch)
    ch.post(10, "AAA:volume_spike", POSTED)
    b = Listener(path, ch, et(10, 2))
    b.catch_up(et(10, 2, 5))
    fade.monitor(b.db, lambda t: None, lambda s: 5.0, now=et(10, 2, 30), up_since=b.state.up_since)
    assert rows(b.db)[0]["status"] == "pending"
    fade.monitor(b.db, lambda t: None, lambda s: 5.0, now=et(10, 5, 30), up_since=b.state.up_since)
    assert (rows(b.db)[0]["status"], rows(b.db)[0]["reason"]) == ("skipped", fade.RESTART_NO_PRINT)


# ---- a restart with an alert already waiting ----------------------------------------------------------------------

def test_a_waiting_short_is_not_entered_on_a_print_from_before_the_restart_nor_after_its_window(path, ch):
    a = started(path, ch)
    a.push(ch.post(10, "AAA:volume_spike,BBB:volume_spike", POSTED), POSTED + timedelta(seconds=10))
    assert [r["status"] for r in rows(a.db)] == ["pending", "pending"]              # received live; then the crash
    db = OpsStore(path)                                                             # back at 10:02:00
    tape, sent = Tape(5.00, et(10, 1, 10)), []
    fade.monitor(db, sent.append, tape, now=et(10, 2, 5), up_since=et(10, 2))
    assert [r["status"] for r in rows(db)] == ["pending", "pending"] and sent == []   # the print predates the restart
    tape.px, tape.at = 5.20, et(10, 2, 30)
    fade.monitor(db, sent.append, tape, now=et(10, 2, 35), up_since=et(10, 2))
    assert [(r["status"], r["est_entry"]) for r in rows(db)] == [("open", 5.20), ("open", 5.20)]


def test_a_short_that_waited_through_its_window_while_the_listener_was_down_is_skipped_not_entered_late(path, ch):
    a = started(path, ch)
    a.push(ch.post(10, "AAA:volume_spike", POSTED), POSTED + timedelta(seconds=10))
    db, sent = OpsStore(path), []                           # back at 10:20: a fresh print exists
    fade.monitor(db, sent.append, Tape(6.00, et(10, 20, 5)), now=et(10, 20, 10), up_since=et(10, 20))
    (r,) = rows(db)
    assert (r["status"], r["reason"], r["est_entry"]) == ("skipped", fade.RESTART_NO_PRINT, None) and sent == []


def test_an_alert_handled_live_enters_exactly_as_before(path, ch):
    a = started(path, ch)
    a.push(ch.post(10, "AAA:volume_spike", POSTED), POSTED + timedelta(seconds=10))
    for up in (None, a.state.up_since):                     # the start-up mark is long before the alert: no effect
        db = OpsStore(path)
        with db.tx() as c:
            c.execute("UPDATE fade_trades SET status='pending', est_entry=NULL, est_entry_at=NULL")
        fade.monitor(db, lambda t: None, Tape(5.0, et(10, 1, 2)), now=et(10, 1, 5), up_since=up)
        assert (rows(db)[0]["status"], rows(db)[0]["est_entry"]) == ("open", 5.0)


# ---- crashes, duplicates, partial replay ----------------------------------------------------------------------------

def test_a_crash_after_the_alert_was_stored_but_before_the_cursor_moved_replays_it_without_a_second_anything(
        path, ch, monkeypatch):
    started(path, ch)
    ch.post(10, "AAA:volume_spike", POSTED)
    b = Listener(path, ch, et(10, 2))
    real = alert_log.advance
    monkeypatch.setattr(alert_log, "advance", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("killed")))
    with pytest.raises(RuntimeError):
        b.catch_up(et(10, 2, 5))
    assert cursor(b.db) == 9 and len(rows(b.db)) == 1 and not b.state.caught_up     # stored; the cursor did not move
    monkeypatch.setattr(alert_log, "advance", real)
    sent, tape = [], Tape(5.4, et(10, 2, 20))
    fade.monitor(b.db, sent.append, tape, now=et(10, 2, 25), up_since=et(10, 2))    # the book trades it meanwhile
    c = Listener(path, ch, et(10, 3))                                               # the next start replays message 10
    assert c.catch_up(et(10, 3, 5)).done and cursor(c.db) == 10 and c.order == [(10, "replay")]
    fade.monitor(c.db, sent.append, tape, now=et(10, 3, 10), up_since=et(10, 3))
    (r,), (e,) = rows(c.db), events(c.db)
    assert (r["status"], r["est_entry"], r["recovered_at"]) == ("open", 5.4, iso(et(10, 2, 5)))   # the FIRST recovery
    assert e["recovered_at"] == iso(et(10, 2, 5)) and len(sent) == 1                # one entry, one text
    assert len(q(c.db, "SELECT 1 FROM decisions WHERE book='short' AND state_from IS NULL")) == 1


def test_a_crash_before_the_alert_was_stored_leaves_the_cursor_and_the_next_pass_handles_it(path, ch):
    started(path, ch)
    ch.post(10, "AAA:volume_spike", POSTED)
    ch.post(11, "BBB:volume_spike", POSTED + timedelta(seconds=30))
    b, calls = Listener(path, ch, et(10, 2)), []

    def flaky(db, a, now):
        calls.append(a.symbol)
        if calls == ["AAA"]:
            raise sqlite3.OperationalError("database is locked")
        return fade.on_alert(db, a, now)

    b.on_alert = flaky
    out = b.catch_up(et(10, 2, 5))
    assert (out.stuck, out.done, cursor(b.db), rows(b.db)) == (10, False, 9, []) and not b.state.caught_up
    assert calls == ["AAA"]                                 # in order: BBB waits behind the alert that failed
    out = b.catch_up(et(10, 2, 8))
    assert out.done and cursor(b.db) == 11 and [r["symbol"] for r in rows(b.db)] == ["AAA", "BBB"]
    assert all(r["via"] == "replay" for r in rows(b.db))


def test_push_and_the_ordered_pass_deliver_the_same_message_and_it_is_handled_once(path, ch):
    a = started(path, ch)
    m = ch.post(10, "AAA:volume_spike", POSTED)
    assert a.push(m, POSTED + timedelta(seconds=10)) and not a.push(m)              # a second push: nothing
    assert a.catch_up(POSTED + timedelta(seconds=15)).done and cursor(a.db) == 10   # the pass finds it handled
    a.state.seen = type(a.state.seen)()                                             # even with the memory gone
    assert a.push(m) and len(rows(a.db)) == 1 and rows(a.db)[0]["via"] == "push" and len(events(a.db)) == 1


def test_a_message_with_several_stocks_moves_the_cursor_only_when_each_one_is_on_record(path, ch):
    started(path, ch)
    ch.post(10, "AAA:volume_spike,BBB:volume_spike,CCC:new_high", POSTED)
    b, fails = Listener(path, ch, et(10, 2)), [1]

    def flaky(db, a, now):
        if a.symbol == "BBB" and fails:
            fails.pop()
            raise RuntimeError("BBB failed")
        return fade.on_alert(db, a, now)

    b.on_alert = flaky
    out = b.catch_up(et(10, 2, 5))                          # a partial replay: two of three stored
    assert out.stuck == 10 and cursor(b.db) == 9 and [r["symbol"] for r in rows(b.db)] == ["AAA", "CCC"]
    assert b.catch_up(et(10, 2, 8)).done and cursor(b.db) == 10
    assert sorted(r["symbol"] for r in rows(b.db)) == ["AAA", "BBB", "CCC"] and len(events(b.db)) == 3   # each once


def test_an_alert_that_keeps_failing_is_given_up_on_loudly_and_never_blocks_the_channel_for_good(path, ch):
    started(path, ch)
    ch.post(10, "AAA:volume_spike,BBB:volume_spike", POSTED)
    ch.post(11, "CCC:volume_spike", POSTED + timedelta(seconds=5))
    b = Listener(path, ch, et(10, 2))

    def broken(db, a, now):
        if a.symbol == "BBB":
            raise RuntimeError("always")
        return fade.on_alert(db, a, now)

    b.on_alert = broken
    for k in range(ar.TRIES - 1):
        assert b.catch_up(et(10, 2, 5 + k)).stuck == 10 and cursor(b.db) == 9
    assert "NOT complete" not in ar.clause(b.db, "2026-11-02")                      # a blip of seconds is not news
    later = datetime.now(timezone.utc) + ar.BEHIND_WARN
    assert "(stopped at message 10)" in ar.clause(b.db, "2026-11-02", now=later) and "catch-up NOT complete since " in \
        ar.clause(b.db, "2026-11-02", now=later)
    out = b.catch_up(et(10, 2, 9))
    assert out.done and out.skipped == 1 and cursor(b.db) == 11
    assert {e["symbol"]: e["status"] for e in events(b.db)} == {"AAA": "pending", "BBB": "failed", "CCC": "pending"}
    (g,) = gaps(b.db)
    assert (g["from_id"], g["to_id"]) == (9, 10) and "BBB" in g["detail"] and g["reason"] == ar.GAVE_UP.format(n=ar.TRIES)
    line = ar.clause(b.db, datetime.now(timezone.utc).date().isoformat())
    assert line.startswith("⚠️ alerts:") and "NOT handled" in line
    assert "1 alerts failed" in ar.clause(b.db, "2026-11-02")   # counted on the day it was received


def test_a_failed_alert_and_its_gap_row_are_written_together_or_not_at_all(path, ch, monkeypatch):
    """Review 2026-11-22: were the failed record and the gap row two transactions, a crash between them would leave
    the alert on record as failed, the next start would find every alert "on record" and move the cursor with no
    gap row and no warning. They are one transaction; and a failed alert is counted however it arrived."""
    a = started(path, ch)
    ch.post(10, "AAA:volume_spike,BBB:volume_spike", et(10, 0, 20))                 # posted while running: via poll

    def broken(db, al, now):
        if al.symbol == "BBB":
            raise RuntimeError("always")
        return fade.on_alert(db, al, now)

    a.on_alert = broken
    for k in range(ar.TRIES - 1):
        assert a.catch_up(et(10, 0, 30 + k)).stuck == 10
    real = alert_log._move
    monkeypatch.setattr(alert_log, "_move", lambda *x: (_ for _ in ()).throw(RuntimeError("killed")))
    with pytest.raises(RuntimeError):
        a.catch_up(et(10, 0, 40))                           # dies inside the give-up, after the failed record
    assert cursor(a.db) == 9 and gaps(a.db) == []
    assert [e["symbol"] for e in events(a.db)] == ["AAA"]   # BBB's failed record rolled back with the gap row
    monkeypatch.setattr(alert_log, "_move", real)
    b = Listener(path, ch, et(10, 1))
    b.on_alert = broken
    for k in range(ar.TRIES):
        b.catch_up(et(10, 1, 5 + k))
    assert cursor(b.db) == 10 and len(gaps(b.db)) == 1
    assert {e["symbol"]: (e["status"], e["via"]) for e in events(b.db)} == {"AAA": ("pending", "poll"), "BBB": ("failed", "replay")}
    assert "1 alerts failed" in ar.clause(b.db, "2026-11-02")


def test_a_long_backlog_is_read_in_pages_oldest_first_and_new_alerts_wait_their_turn(path, ch):
    started(path, ch)
    for i in range(250):
        ch.post(10 + i, f"S{i}:halted", et(9, 50) + timedelta(seconds=i))
    b = Listener(path, ch, et(10, 2))
    ch.calls.clear()
    out = b.catch_up(et(10, 2, 5))
    assert (out.handled, out.done) == (ar.MAX_PER_PASS, False) and not b.state.caught_up and cursor(b.db) == 209
    assert ch.calls == [(9, 100), (109, 100)]               # two pages, then the pass's bound
    live = ch.post(300, "AAA:volume_spike", et(10, 2, 6))
    assert not b.push(live, et(10, 2, 7)) and b.state.kick and rows(b.db) == []     # not before the backlog
    assert ar.wait_s(b.state, 15, True) == ar.BEHIND_EVERY
    out = b.catch_up(et(10, 2, 9))
    assert out.done and out.handled == 51 and b.state.caught_up and cursor(b.db) == 300
    assert [i for i, _ in b.order] == list(range(10, 260)) + [300]                  # strictly in the channel's order
    assert len(events(b.db)) == 251 and rows(b.db)[0]["via"] == "poll" and gaps(b.db) == []
    assert ar.wait_s(b.state, 15, True) == 15 and ar.wait_s(b.state, 15, False) == ar.OFF_EVERY
    assert ar.wait_s(b.state, 0, True) == ar.OFF_EVERY      # the fast check switched off: the cursor still moves


def test_deleted_messages_and_holes_in_the_ids_need_no_special_case(path, ch):
    started(path, ch)
    del ch.msgs[9]                                          # the cursor's own message was deleted meanwhile
    for i in (10, 11, 15, 40):
        ch.post(i, f"S{i}:volume_spike", POSTED + timedelta(seconds=i))
    b = Listener(path, ch, et(10, 2))
    assert b.catch_up(et(10, 2, 5)).done and cursor(b.db) == 40
    assert [e["message_id"] for e in events(b.db)] == [10, 11, 15, 40] and gaps(b.db) == []


def test_a_backlog_too_old_to_matter_is_passed_in_one_recorded_jump_never_silently(path, ch):
    started(path, ch, up=et(9, 45, day=2))
    for i in (10, 11, 12):
        ch.post(i, f"S{i}:volume_spike", et(10, 0, day=3) + timedelta(minutes=i))    # 2026-11-03
    ch.post(13, "AAA:volume_spike", et(10, 0, 20, day=16))                           # two weeks later: today
    b = Listener(path, ch, et(10, 2, day=16))
    out = b.catch_up(et(10, 2, 5, day=16))
    assert out.done and (out.skipped, out.handled) == (1, 1) and cursor(b.db) == 13
    (g,) = gaps(b.db)
    assert (g["from_id"], g["to_id"], g["reason"]) == (9, 12, ar.TOO_OLD.format(d=ar.HORIZON.days))
    assert [e["message_id"] for e in events(b.db)] == [13] and b.order == [(13, "replay")]
    line = ar.clause(b.db, datetime.now(timezone.utc).date().isoformat())
    assert "of the channel NOT handled (message ids " in line and "9..12: older than 8 days" in line


def test_an_unreadable_message_is_written_down_and_the_next_one_is_still_handled(path, ch):
    started(path, ch)
    ch.post(10, "BAD", POSTED)
    ch.post(11, "AAA:volume_spike", POSTED + timedelta(seconds=5))
    b = Listener(path, ch, et(10, 2))
    out = b.catch_up(et(10, 2, 5))
    assert out.done and (out.skipped, out.handled) == (1, 1) and cursor(b.db) == 11
    (g,) = gaps(b.db)
    assert (g["to_id"], g["reason"]) == (10, ar.UNREADABLE) and [r["symbol"] for r in rows(b.db)] == ["AAA"]


def test_an_unreachable_channel_at_start_keeps_the_cursor_and_the_push_path_waits(path, ch):
    started(path, ch)
    ch.post(10, "AAA:volume_spike", et(10, 0, 20))
    b = Listener(path, ch, et(10, 6))
    ch.fail = 1
    with pytest.raises(OSError):
        b.catch_up(et(10, 6, 5))
    assert cursor(b.db) == 9 and not b.state.caught_up
    assert "catch-up NOT complete since " in ar.clause(b.db, "2026-11-02", now=datetime.now(timezone.utc) + ar.BEHIND_WARN)
    assert "(OSError)" in ar.clause(b.db, "2026-11-02", now=datetime.now(timezone.utc) + ar.BEHIND_WARN)   # not silent
    live = ch.post(11, "AAA:volume_spike", et(10, 6, 10))                           # a new alert of the same stock
    assert not b.push(live, et(10, 6, 11)) and rows(b.db) == []                     # waits: order first
    assert b.catch_up(et(10, 6, 20)).done and b.order == [(10, "replay"), (11, "poll")]
    assert [(e["message_id"], e["status"]) for e in events(b.db)] == [(10, "skipped"), (11, "duplicate")]
    assert [(r["status"], r["reason"]) for r in rows(b.db)] == [("skipped", "alert received late")]
    assert ar.clause(b.db, "2026-11-02").startswith("alerts: in order, 1 recovered after a restart")


# ---- switches, limits and positions survive the restart ------------------------------------------------------------

def test_switches_limits_and_the_one_trade_per_stock_rule_hold_for_a_recovered_alert(path, ch, monkeypatch):
    a = started(path, ch)
    a.push(ch.post(10, "AAA:volume_spike", et(9, 55)), et(9, 55, 10))               # AAA already has today's row
    a.db.set_flag(fade.OFF_FLAG, "1")                                               # the owner switched shorts off
    ch.post(11, "AAA:volume_spike,BBB:volume_spike", POSTED)                        # ... then the listener died
    b = Listener(path, ch, et(10, 2))
    b.catch_up(et(10, 2, 5))
    assert {e["symbol"]: e["status"] for e in events(b.db) if e["message_id"] == 11} == {
        "AAA": "duplicate", "BBB": "pending"}                                       # no second AAA row after a restart
    sent = []
    fade.monitor(b.db, sent.append, Tape(5.0, et(10, 2, 20)), now=et(10, 2, 25), up_since=et(10, 2))
    bbb = next(r for r in rows(b.db) if r["symbol"] == "BBB")
    assert (bbb["status"], bbb["reason"]) == ("filtered", fade.OFF_REASON) and bbb["est_entry"] is None and sent == []
    monkeypatch.setattr(fade, "MAX_OPEN", 1)                                        # the exposure limit, from the rows
    with b.db.tx() as c:
        c.execute("UPDATE fade_trades SET status='open', est_entry=4.0, est_last=4.0, est_entry_at=? WHERE symbol='AAA'",
                  (iso(et(9, 56)),))
    ch.post(12, "CCC:volume_spike", et(10, 3))
    ch.post(13, "DDD:volume_spike", et(10, 3, 10))
    c2 = Listener(path, ch, et(10, 4))
    c2.catch_up(et(10, 4, 5))
    assert next(e for e in events(c2.db) if e["symbol"] == "CCC")["reason"] == "runaway guard: 1 open"
    c2.db.set_flag("paused", "1")
    ch.post(14, "EEE:volume_spike", et(10, 4))
    Listener(path, ch, et(10, 5)).catch_up(et(10, 5, 5))
    assert next(e for e in events(c2.db) if e["symbol"] == "EEE")["reason"] == "paused"


def test_an_open_short_is_still_managed_after_a_restart(path, ch):
    a = started(path, ch)
    a.push(ch.post(10, "AAA:volume_spike", POSTED), POSTED + timedelta(seconds=10))
    sent = []
    fade.monitor(a.db, sent.append, Tape(5.0, et(10, 1, 2)), now=et(10, 1, 5), up_since=a.state.up_since)
    db = OpsStore(path)                                     # restart with the short open
    fade.monitor(db, sent.append, Tape(6.5, et(10, 9, 58)), now=et(10, 10), up_since=et(10, 9, 59))   # through the 20% stop
    (r,) = rows(db)
    assert r["status"] == "est_closed" and r["est_exit"] == 6.5 and len(sent) == 2  # exits are never held back


# ---- position protection does not depend on the alert channel -----------------------------------------------------

def _exit_delay(path, ch, *, fetch=None, slow=0.0, backlog=0):
    """An open short, its monitor loop running as the listener runs it (a worker thread, every 20 ms), and a catch-up
    under way. The price goes through the stop: returns (seconds until the exit is booked, catch-up finished?)."""
    a = started(path, ch)
    a.push(ch.post(10, "AAA:volume_spike", POSTED), POSTED + timedelta(seconds=10))
    tape, sent = Tape(5.0, et(10, 1, 2)), []
    fade.monitor(a.db, sent.append, tape, now=et(10, 1, 5), up_since=a.state.up_since)
    assert rows(a.db)[0]["status"] == "open"
    for i in range(backlog):
        ch.post(100 + i, f"S{i}:halted", et(10, 2) + timedelta(seconds=i))
    b = Listener(path, ch, et(10, 9))                       # a restart with the short open

    def handle(m, how):
        time.sleep(slow)                                    # a slow replay: the database is busy, say
        return b.handle(m, how)

    async def run():
        async def monitor_loop():
            while True:
                await asyncio.to_thread(fade.monitor, b.db, sent.append, tape, et(10, 10), None, None, None, None,
                                        b.state.up_since)
                await asyncio.sleep(0.02)

        replay = asyncio.create_task(ar.catch_up(b.db, b.state, fetch or ch.fetch, handle, ch.newest, ch.before,
                                                 now=lambda: et(10, 9, 5)))
        mon = asyncio.create_task(monitor_loop())
        await asyncio.sleep(0.15)                           # the catch-up is under way (or hanging)
        tape.px, tape.at, t0 = 6.5, et(10, 9, 58), time.monotonic()     # through the 20% stop
        while rows(b.db)[0]["status"] != "est_closed":
            assert time.monotonic() - t0 < 5, "the exit was never booked"
            await asyncio.sleep(0.01)
        took, done = time.monotonic() - t0, replay.done()
        for t in (replay, mon):
            t.cancel()
        return took, done

    return asyncio.run(run())


def test_an_alert_channel_that_never_answers_does_not_hold_an_exit(path, ch):
    async def hangs(after, limit):
        await asyncio.Event().wait()

    took, finished = _exit_delay(path, ch, fetch=hangs, backlog=1)
    assert took < 0.5 and not finished                      # the stop was booked while the channel still hung


def test_a_slow_replay_does_not_hold_an_exit(path, ch):
    """Review 2026-11-23: the replay of #200 handled each alert ON the event loop, so one alert waiting 1 s for the
    database held back the next stop check by as long. Its work now runs on a thread of its own."""
    took, finished = _exit_delay(path, ch, slow=1.0, backlog=6)
    assert took < 0.5 and not finished                      # booked while the replay was still crawling


def test_a_large_backlog_does_not_hold_an_exit(path, ch):
    took, _ = _exit_delay(path, ch, backlog=200)
    assert took < 0.5


# ---- the long book ---------------------------------------------------------------------------------------------------

T1 = pd.Timestamp("2026-11-09 15:01", tz="UTC")
LONG_DAY = pd.DataFrame([{"ts": T1 + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                         for i, (o, h, lo, c, v) in enumerate(
                             [(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.65, 500),
                              (10.65, 11.6, 10.6, 11.5, 800)] + [(11.5, 11.6, 11.4, 11.5, 100)] * 50)])


@pytest.fixture()
def one_minute_setups(monkeypatch):
    monkeypatch.setattr(lb, "LIVE_KINDS", ("L5v2", "L5", "L4b", "L6", "L4a", "L7b", "L7a"))
    monkeypatch.setattr(lb, "LIVE_WATCH", pt.WATCH)


def long_alert(db, recovered_at=None):
    posted = pd.Timestamp("2026-11-09 15:00:20", tz="UTC")
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                  "weight, created_at, via, recovered_at) VALUES ('ABCD','2026-11-09','volume_spike',?,?,?,'pending',"
                  "NULL,0.05,?,?,?)", (posted.isoformat(), CH, 77, posted.isoformat(),
                                       "replay" if recovered_at else "push", recovered_at))


@pytest.mark.parametrize("up, recovered, status", [
    (None, None, "open"),                                   # handled live: bought (the fill candle is 15:03)
    ("2026-11-09T15:02:30+00:00", None, "open"),            # back before the fill candle began: watched live
    ("2026-11-09T15:03:30+00:00", None, "missed"),          # back inside the fill candle: its price is history
    (None, "2026-11-09T15:03:30+00:00", "missed"),          # the alert itself was recovered inside it
    ("2026-11-09T14:00:00+00:00", "2026-11-09T15:03:30+00:00", "missed"),
])
def test_a_long_fill_from_before_the_listener_was_back_is_missed_never_bought(tmp_path, one_minute_setups, up, recovered,
                                                                             status):
    db, sent = OpsStore(tmp_path / "ops.db"), []
    ap.arm(db, T1.to_pydatetime())
    long_alert(db, recovered)
    now = (T1 + pd.Timedelta(minutes=3)).to_pydatetime()    # 15:04:00, the 15:03 candle has closed
    lb.tick(db, lambda sym, at: LONG_DAY, sent.append, now=now, up_since=datetime.fromisoformat(up) if up else None)
    (t,) = q(db, "SELECT status, est_entry FROM long_trades")
    assert t["status"] == status and (len(sent) == 1) == (status == "open")        # a missed fill is never texted
    (d,) = q(db, "SELECT facts FROM decisions WHERE book='long'")
    why = json.loads(d["facts"]).get("missed_why")                                 # countable in the record
    assert why == ("fill candle began before the listener was back" if status == "missed" else None)


# ---- the accounting ----------------------------------------------------------------------------------------------------

def test_recovered_alerts_are_counted_by_stage_and_the_nightly_line_always_says_where_the_channel_stands(path, ch, tmp_path):
    a = started(path, ch)
    day = "2026-11-02"
    assert ar.clause(a.db, day) == "alerts: in order, none recovered, no gap"
    ch.post(10, "OLD:volume_spike", et(9, 50))              # too old when recovered
    ch.post(11, "NWS:news", et(10, 0, 10))                  # a news alert: filtered by the rule, as always
    ch.post(12, "AAA:volume_spike,BBB:volume_spike", POSTED)
    b = Listener(path, ch, et(10, 2))
    b.catch_up(et(10, 2, 5))
    quotes = {"AAA": Tape(5.4, et(10, 2, 20)), "BBB": Tape(80.0, et(10, 2, 20))}    # BBB is outside $1-50 at entry
    quote = lambda s, since=None: quotes[s](s, since)  # noqa: E731
    quote.since_aware = True
    fade.monitor(b.db, lambda t: None, quote, now=et(10, 2, 25), up_since=et(10, 2))
    acc = ar.account(b.db, day, day)
    assert {k: acc[k] for k in ("recovered", "eligible", "attempted", "simulated_filled", "too_old", "failed")} == {
        "recovered": 4, "eligible": 2, "attempted": 1, "simulated_filled": 0, "too_old": 1, "failed": 0}
    ledger = tmp_path / "ledger.jsonl"
    aaa = next(r["id"] for r in rows(b.db) if r["symbol"] == "AAA")
    ledger.write_text(json.dumps({"book": "short", "id": aaa, "label": "MODELED", "via": "replay"}) + "\n")
    assert ar.account(b.db, day, day, ledger)["simulated_filled"] == 1
    assert ar.clause(b.db, day) == ("alerts: in order, 4 recovered after a restart (2 eligible, 1 entered on paper, "
                                    "1 too old to trade; longs 0 bought, 0 missed), no gap")
    assert ar.clause(b.db, "2026-11-03", "2026-11-02").endswith(
        "(since 11-02: 4 recovered, 2 eligible, 1 entered, 0 priced by the model)")
    line = decision_health.line(b.db, day)
    assert "; alerts: in order, 4 recovered after a restart" in line and line.index("alerts:") < line.index("database:")


def test_the_clause_never_raises_into_the_summary(path, monkeypatch):
    monkeypatch.setattr(ar, "account", lambda *a, **k: 1 / 0)
    assert ar.clause(OpsStore(path), "2026-11-02") == "⚠️ alerts: check failed (ZeroDivisionError)"
