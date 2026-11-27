"""Where a short's live exit estimate came from (owner 2026-11-27: "Approve adding the exit-price source timestamp
as a narrowly scoped recording change ... Do not change exit eligibility, pricing rules, or historical results").

KIDZ 2026-11-27: the book closed the short at 13:00:05 ET at $1.045, the free feed's last round-lot print, made at
12:58:52. The row held the decision time and the price and nothing about the print. These tests pin the record and
that the exit itself (when, at what price, for what reason, with what text) is the same with and without it."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from sgt.fast import fade, tape_check as tc
from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore, iso

ET = ZoneInfo("America/New_York")
T10 = datetime(2026, 11, 2, 10, 0, tzinfo=ET).astimezone(timezone.utc)       # a Monday session in the past
SRC = "a named reader"
NEW = ("exit_decided_at", "exit_src", "exit_src_px", "exit_src_at", "exit_src_age_s", "exit_src_note")


class Feed:
    """A quote whose every look is scripted: (price or None, print time or None, why or None)."""
    since_aware = True

    def __init__(self, source=SRC):
        self.next, self.seen = (None, None, None), {}
        if source is not None:
            self.source = source

    def __call__(self, symbol, since=None):
        px, at, why = self.next
        self.seen = {symbol: {"at": None if at is None else at if isinstance(at, str) else at.isoformat(), "why": why}}
        return px


def book(tmp_path, name="ops.db"):
    return OpsStore(tmp_path / name)


def row(db):
    with db.tx() as c:
        return dict(c.execute("SELECT * FROM fade_trades ORDER BY id").fetchone())


def opened(db, quote, px=10.0):
    """One armed short, opened at 10:00:20 ET on a print 2 s old."""
    ap.arm(db, T10)
    fade.on_alert(db, fade.LiveAlert("ABC", "volume_spike", T10 - timedelta(seconds=10), "-1", 1, None), now=T10)
    sent = []
    at = T10 + timedelta(seconds=20)
    if isinstance(quote, Feed):
        quote.next = (px, at - timedelta(seconds=2), None)
    fade.monitor(db, sent.append, quote, now=at)
    assert row(db)["status"] == "open"
    return sent


def test_a_fresh_exit_price_is_recorded_with_its_print_time_reader_and_age(tmp_path):
    db, q = book(tmp_path), Feed()
    sent = opened(db, q)
    assert row(db)["est_last_print_at"] == (T10 + timedelta(seconds=18)).isoformat()     # the entry mark's own print
    now = T10 + timedelta(minutes=61)
    q.next = (9.0, now - timedelta(seconds=3), None)
    fade.monitor(db, sent.append, q, now=now)
    r = row(db)
    assert (r["status"], r["reason"], r["est_exit"], r["est_exit_at"]) == ("est_closed", "60 min", 9.0, iso(now))
    assert r["exit_decided_at"] == iso(now)                                  # the decision time, in its own column
    assert (r["exit_src"], r["exit_src_px"]) == (SRC, 9.0)
    assert r["exit_src_at"] == (now - timedelta(seconds=3)).isoformat()      # when that price occurred on the reader
    assert (r["exit_src_age_s"], r["exit_src_note"]) == (3.0, fade.AT_OBSERVED)
    closed = [d for d in db.decisions("short", r["id"]) if d["state_to"] == "est_closed"]
    assert len(closed) == 1 and '"exit_src_age_s": 3.0' in closed[0]["facts"]      # the decision log carries it too


def test_a_stale_exit_price_is_used_exactly_as_before_and_its_age_is_on_record(tmp_path):
    """KIDZ's shape: the decision 72 s after the reader's last print. The age is a record: it classifies nothing,
    and the exit is the same exit, at the same price, with the same text, as on a book whose quote records nothing.
    The print time says when that price occurred, not that an order could have been filled there."""
    db, q, plain = book(tmp_path), Feed(), book(tmp_path, "plain.db")
    sent, sent_plain = opened(db, q), opened(plain, lambda s, since=None: 10.0)
    now = T10 + timedelta(minutes=60, seconds=25)            # 5 s after the hour is up (in at 10:00:20)
    q.next = (9.0, now - timedelta(seconds=72.115), None)
    fade.monitor(db, sent.append, q, now=now)
    fade.monitor(plain, sent_plain.append, lambda s, since=None: 9.0, now=now)
    r, p = row(db), row(plain)
    old = [k for k in r if k not in NEW and k not in ("est_last_print_at", "est_print_at", "created_at")]
    assert {k: r[k] for k in old} == {k: p[k] for k in old}                  # nothing but the record differs
    assert (r["status"], r["reason"], r["est_exit"], r["est_exit_at"]) == ("est_closed", "60 min", 9.0, iso(now))
    assert sent == sent_plain and len(sent) == 2                             # the same two texts, word for word
    assert (r["exit_src_px"], r["exit_src_age_s"], r["exit_src_note"]) == (9.0, 72.115, fade.AT_OBSERVED)
    assert r["exit_src_at"] == (now - timedelta(seconds=72.115)).isoformat()
    # the book with a quote that records nothing: the missing time is said, never rebuilt
    assert (p["exit_src"], p["exit_src_px"]) == (fade.SRC_UNNAMED, 9.0)
    assert (p["exit_src_at"], p["exit_src_age_s"], p["exit_src_note"]) == (None, None, fade.AT_NONE)
    assert p["exit_decided_at"] == iso(now)


def test_no_price_at_the_decision_carries_the_last_mark_with_that_marks_own_print_time(tmp_path):
    db, q = book(tmp_path), Feed()
    sent = opened(db, q)
    look = T10 + timedelta(minutes=30)
    q.next = (9.5, look - timedelta(seconds=4), None)
    fade.monitor(db, sent.append, q, now=look)
    assert (row(db)["est_last"], row(db)["est_last_print_at"]) == (9.5, (look - timedelta(seconds=4)).isoformat())
    now = T10 + timedelta(minutes=61)
    refused = now - timedelta(seconds=200)             # the reader saw a print and refused it as a price
    q.next = (None, refused, "free-feed print 10:57:40 ET is over 90 s old after a large move (likely halted)")
    fade.monitor(db, sent.append, q, now=now)
    r = row(db)
    assert (r["status"], r["reason"], r["est_exit"]) == ("est_closed", "60 min", 9.5)     # the last mark, as before
    assert r["exit_src"] == (f"{SRC}, {fade.SRC_CARRIED}; at the decision: free-feed print 10:57:40 ET is over 90 s "
                             "old after a large move (likely halted)")
    assert r["exit_src_px"] == 9.5
    assert r["exit_src_at"] == (look - timedelta(seconds=4)).isoformat()     # the carried mark's print, not the
    assert r["exit_src_at"] != refused.isoformat()                           # refused one's
    assert (r["exit_src_age_s"], r["exit_src_note"]) == (31 * 60 + 4.0, fade.AT_OBSERVED)


def test_an_unavailable_exit_price_without_a_recorded_print_time_is_marked_missing(tmp_path):
    db = book(tmp_path)
    sent = opened(db, lambda s, since=None: 10.0)                            # a quote that records nothing
    now = T10 + timedelta(minutes=61)
    fade.monitor(db, sent.append, lambda s, since=None: None, now=now)       # and no price at the decision
    r = row(db)
    assert (r["status"], r["reason"], r["est_exit"]) == ("est_closed", "60 min", 10.0)
    assert r["exit_src"] == f"{fade.SRC_UNNAMED}, {fade.SRC_CARRIED}; at the decision: no price"
    assert (r["exit_src_px"], r["exit_src_at"], r["exit_src_age_s"]) == (10.0, None, None)
    assert (r["exit_src_note"], r["exit_decided_at"]) == (fade.AT_CARRIED_NONE, iso(now))


def test_a_newer_mark_without_a_print_time_never_keeps_an_older_marks_time(tmp_path):
    db, q = book(tmp_path), Feed()
    sent = opened(db, q)
    q.next = (9.4, None, None)
    fade.monitor(db, sent.append, q, now=T10 + timedelta(minutes=10))
    assert (row(db)["est_last"], row(db)["est_last_print_at"]) == (9.4, None)
    now = T10 + timedelta(minutes=61)
    q.next = (None, None, "the quote request failed")
    fade.monitor(db, sent.append, q, now=now)
    r = row(db)
    assert (r["est_exit"], r["exit_src_at"], r["exit_src_age_s"]) == (9.4, None, None)
    assert r["exit_src_note"] == fade.AT_CARRIED_NONE and r["exit_src"].endswith("the quote request failed")


def test_a_stop_exit_is_recorded_the_same_way(tmp_path):
    db, q = book(tmp_path), Feed()
    sent = opened(db, q)
    now = T10 + timedelta(minutes=5)
    q.next = (12.5, now - timedelta(seconds=1), None)
    fade.monitor(db, sent.append, q, now=now)
    r = row(db)
    assert (r["reason"], r["est_exit"], r["exit_src_px"], r["exit_src_age_s"]) == ("stop", 12.5, 12.5, 1.0)


def test_a_record_that_cannot_be_built_never_stops_or_changes_the_exit(tmp_path, monkeypatch, caplog):
    def broken(*a, **k):
        raise RuntimeError("no record")
    monkeypatch.setattr(fade, "exit_source", broken)
    db, q = book(tmp_path), Feed()
    q.seen = {"ABC": "not a dict"}
    sent = opened(db, q)
    now = T10 + timedelta(minutes=61)
    q.next = (9.0, now - timedelta(seconds=3), None)
    fade.monitor(db, sent.append, q, now=now)
    r = row(db)
    assert (r["status"], r["reason"], r["est_exit"], r["est_exit_at"]) == ("est_closed", "60 min", 9.0, iso(now))
    assert all(r[k] is None for k in NEW) and len(sent) == 2 and "exit source not recorded" in caplog.text
    late, sent2 = book(tmp_path, "late.db"), []
    short(late)
    assert tc.check(late, sent2.append, tape(("10:54", 1.81, 2.05)), now=minute("11:11")) == 1     # the stop stands
    r = row(late)
    assert (r["reason"], r["est_exit_at"]) == (tc.LATE, iso(minute("10:54"))) and all(r[k] is None for k in NEW)


NOW = datetime(2026, 11, 27, 18, 0, 5, 89000, tzinfo=timezone.utc)


@pytest.mark.parametrize("at, when, age, note", [
    ("2026-11-27T17:58:52.974000+00:00", "2026-11-27T17:58:52.974000+00:00", 72.115, fade.AT_OBSERVED),
    (NOW - timedelta(seconds=2), (NOW - timedelta(seconds=2)).isoformat(), 2.0, fade.AT_OBSERVED),
    (None, None, None, fade.AT_NONE),
    ("", None, None, fade.AT_NONE),
    ("2026-11-27T17:58:52", None, None, fade.AT_UNREADABLE),               # no zone: not a time we can place
    ("12:58:52 ET", None, None, fade.AT_UNREADABLE),
    (1764266332, None, None, fade.AT_NONE),                                # not a string at all
])
def test_the_source_record_never_invents_a_time(at, when, age, note):
    s = fade.exit_source(NOW, SRC, 1.045, at)
    assert s == {"exit_decided_at": iso(NOW), "exit_src": SRC, "exit_src_px": 1.045, "exit_src_at": when,
                 "exit_src_age_s": age, "exit_src_note": note}
    assert set(s) == set(NEW)


# ---- the official-tape stop check replaces the estimate, so it replaces the record of its source --------------------
IN = datetime(2026, 11, 17, 15, 3, 11, tzinfo=timezone.utc)                 # 10:03:11 ET, short 1.54 -> stop 1.848


def minute(hm):
    return datetime.fromisoformat(f"2026-11-17T{hm}:00").replace(tzinfo=ET).astimezone(timezone.utc)


def tape(*bars_):
    import pandas as pd

    f = pd.DataFrame([{"ts": pd.Timestamp(minute(hm)), "open": o, "high": h} for hm, o, h in bars_])
    return lambda symbol, start, end: f


def short(db, status="open", reason=fade.VETO, exit_at=None, exit_px=None):
    with db.tx() as c:
        return c.execute(
            "INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, weight,"
            " est_entry, est_entry_at, est_last, est_last_print_at, est_exit, est_exit_at, exit_decided_at, exit_src,"
            " exit_src_px, exit_src_at, exit_src_age_s, exit_src_note, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("MPU", "2026-11-17", "gaining_momentum", iso(IN - timedelta(seconds=69)), "-1", 1, status, reason, 0.0125,
             1.54, iso(IN), exit_px or 1.54, iso(IN), exit_px, None if exit_at is None else iso(exit_at),
             None if exit_at is None else iso(exit_at), None if exit_at is None else SRC, exit_px,
             None if exit_at is None else iso(exit_at - timedelta(seconds=5)), None if exit_at is None else 5.0,
             None if exit_at is None else fade.AT_OBSERVED, iso(IN))).lastrowid


def test_a_late_official_stop_records_the_candle_as_the_source_and_keeps_the_two_times_apart(tmp_path):
    db, sent = book(tmp_path), []
    short(db)
    now = minute("11:11")
    assert tc.check(db, sent.append, tape(("10:54", 1.81, 2.05)), now=now) == 1
    r = row(db)
    assert (r["status"], r["reason"]) == ("est_closed", tc.LATE) and r["est_exit"] == pytest.approx(1.54 * 1.2)
    assert r["est_exit_at"] == iso(minute("10:54"))                          # unchanged: the candle's start
    assert r["exit_decided_at"] == iso(now)                                  # new: when the check decided
    assert (r["exit_src"], r["exit_src_px"]) == (tc.SRC, pytest.approx(1.54 * 1.2))
    assert (r["exit_src_at"], r["exit_src_age_s"]) == (minute("10:54").isoformat(), 17 * 60.0)
    assert r["exit_src_note"] == tc.AT_LEVEL and r["est_last_print_at"] is None      # a rule price, not a print


def test_a_corrected_cover_replaces_the_live_source_and_a_gap_open_is_named(tmp_path):
    db, sent = book(tmp_path), []
    rid = short(db, "est_closed", "60 min", minute("11:03") + timedelta(seconds=11), 1.75)
    now = minute("11:12")
    assert tc.check(db, sent.append, tape(("10:54", 1.95, 2.05)), now=now) == 1      # opened above the 1.848 stop
    r = row(db)
    assert (r["reason"], r["est_exit"], r["exit_src"]) == (tc.LATE, 1.95, tc.SRC)
    assert (r["exit_src_px"], r["exit_src_note"], r["exit_decided_at"]) == (1.95, tc.AT_OPEN, iso(now))
    assert r["exit_src_at"] == minute("10:54").isoformat() and r["exit_src_age_s"] == 18 * 60.0
    assert [d["state_to"] for d in db.decisions("short", rid)] == ["est_closed"]      # the correction is logged


def test_old_rows_keep_every_new_column_empty(tmp_path):
    db = book(tmp_path)
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason,"
                  " est_entry, est_entry_at, est_exit, est_exit_at, created_at) VALUES "
                  "('KIDZ','2026-11-27','volume_spike','x','-1',9,'est_closed','close',1.24,'a',1.045,'b','c')")
    r = row(db)
    assert all(r[k] is None for k in (*NEW, "est_last_print_at"))            # nothing is filled in afterwards
    assert date.fromisoformat(r["session"]) == date(2026, 11, 27)
