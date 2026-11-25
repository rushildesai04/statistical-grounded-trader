"""The existing stop of an open long, looked for between candles on the free feed's last trade (owner 2026-11-25:
"for paper measurement only, with the same stop level and no changes to registered simulation results. Do not treat
it as real stop protection")."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from sgt.fast import long_book as lb
from sgt.fast import long_shadow as ls
from sgt.fast import patterns as pt
from sgt.ops.sms_style import bold, italic
from sgt.ops.store import OpsStore

from tests.fast.test_long_book import DAY, POSTED, T1, alert_row, at, bars, feed, one_minute_setups, trades  # noqa: F401

CLOSE = pd.Timestamp("2026-11-09 21:00", tz="UTC")
ENTRY_TS = T1 + 2 * pt.BAR                                   # the fill candle, 15:03
BOOKED = T1 + 3 * pt.BAR                                     # booked when it closed, 15:04:00


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def row(**kw):
    r = {"id": 7, "symbol": "ABCD", "kind": "L4b", "rules": lb.RULES, "stop": 10.2, "target": 11.25, "est_entry": 10.55,
         "trigger": 10.55, "created_at": BOOKED.isoformat(), "entry_ts": ENTRY_TS.isoformat(), "session": "2026-11-09"}
    return {**r, **kw}


def reader(px, printed):
    """The free feed's raw last print, as ``fade_live.iex_last`` hands it over."""
    def last(symbol):
        last.calls.append(symbol)
        return {"px": px, "at": printed.isoformat() if hasattr(printed, "isoformat") else printed}
    last.calls = []
    return last


def closed(n):
    """The first ``n`` candles of the day: what the book has when the n-th has closed."""
    return DAY.iloc[:n].reset_index(drop=True)


def look(px, printed, now=None, r=None, f=None):
    now = now or (BOOKED + pd.Timedelta(seconds=45)).to_pydatetime()
    return lb.between_candles(r or row(), closed(3) if f is None else f, ENTRY_TS, now, reader(px, printed), CLOSE)


def test_a_print_at_the_stop_closes_at_the_stop_one_tick_above_does_not_and_a_print_under_it_books_the_print():
    printed = BOOKED + pd.Timedelta(seconds=40)
    t, seen = look(10.2, printed)
    assert (t.reason, t.exit, t.exit_ts, t.entry, t.entry_ts) == ("stop", 10.2, printed, 10.55, ENTRY_TS)
    assert t.gross == pytest.approx(10.2 / 10.55 - 1)
    assert seen == {"by": "a free-feed print between candles", "print_px": 10.2, "print_at": printed.isoformat(),
                    "looked_at": (BOOKED + pd.Timedelta(seconds=45)).isoformat(),
                    "last_closed_candle": ENTRY_TS.isoformat(), "stop": 10.2}
    assert look(10.2001, printed) is None                                      # the level is the stop, unchanged
    t, seen = look(10.05, printed)
    assert t.exit == 10.05 and seen["print_px"] == 10.05                       # under the stop: the print, not the stop


def test_bad_or_out_of_place_data_is_no_decision():
    ok = BOOKED + pd.Timedelta(seconds=40)
    assert look(10.1, BOOKED - pd.Timedelta(seconds=1)) is None                # printed before the long was booked
    assert look(10.1, BOOKED) is not None                                      # at the booking instant counts
    assert look(10.1, BOOKED + pd.Timedelta(seconds=50)) is None               # "printed" after this look: not believed
    for px in (None, 0, -1.0, float("nan"), float("inf"), True, "10.1"):
        assert look(px, ok) is None, px
    for junk in ({"why": "the request failed (HTTP 500)"}, None, "junk", {}):   # no print to read
        assert lb.between_candles(row(), closed(3), ENTRY_TS, ok.to_pydatetime(), lambda s, j=junk: j, CLOSE) is None
    for when in ("not a time", "2026-11-09T15:04:40", None, ""):               # unreadable, or without a zone
        assert look(10.1, when) is None, when
    assert lb.between_candles(row(), closed(3), ENTRY_TS, ok.to_pydatetime(), None, CLOSE) is None      # no reader

    def boom(symbol):
        raise RuntimeError("down")
    assert lb.between_candles(row(), closed(3), ENTRY_TS, ok.to_pydatetime(), boom, CLOSE) is None
    assert look(10.1, ok, r=row(stop=None)) is None and look(10.1, ok, r=row(created_at="2026-11-09T15:04:00")) is None


def test_it_never_pre_empts_an_exit_that_is_already_determined():
    ok = BOOKED + pd.Timedelta(seconds=40)
    # the hold window has ended: the time exit belongs to the closed candles
    late = ENTRY_TS + pt.HOLD
    assert look(10.1, late - pd.Timedelta(seconds=1), now=late.to_pydatetime()) is None
    assert look(10.1, late - pd.Timedelta(seconds=2), now=(late - pd.Timedelta(seconds=1)).to_pydatetime()) is not None
    # the session has closed
    assert lb.between_candles(row(), closed(3), ENTRY_TS, ok.to_pydatetime(), reader(10.1, ok),
                              pd.Timestamp(ok) - pd.Timedelta(seconds=1)) is None
    # rules A2, fill candle closed RED (close under the entry): out at the next open, whatever prints meanwhile
    red = closed(3).copy()
    red.loc[2, "close"] = 10.50
    assert look(10.1, ok, f=red) is None
    assert look(10.1, ok, f=red, r=row(rules=lb.RULES_BASE)) is not None        # no red-candle exit under B1: the stop
    assert look(10.1, ok, f=closed(2)) is not None                              # fill candle not closed yet: not red


def test_the_live_book_closes_once_keeps_the_record_and_says_what_it_is(db):
    alert_row(db)
    sent, printed = [], BOOKED + pd.Timedelta(seconds=20)
    quiet = reader(10.61, T1 + pd.Timedelta(minutes=2, seconds=5))             # an old print above the stop
    assert lb.tick(db, feed(DAY), sent.append, now=at(3), stop_print=quiet) == 1
    assert trades(db)[0]["status"] == "open" and trades(db)[0]["stop_seen"] is None
    falling = reader(10.15, printed)
    lb.tick(db, feed(DAY), sent.append, now=at(3, 30), stop_print=falling)
    (t,) = trades(db)
    assert (t["status"], t["reason"], t["est_exit"], t["est_exit_at"]) == ("est_closed", "stop", 10.15, printed.isoformat())
    assert json.loads(t["stop_seen"]) == {"by": "a free-feed print between candles", "print_px": 10.15,
                                          "print_at": printed.isoformat(), "looked_at": pd.Timestamp(at(3, 30)).isoformat(),
                                          "last_closed_candle": ENTRY_TS.isoformat(), "stop": 10.2}
    assert (t["stop"], t["target"], t["est_entry"]) == (10.2, pytest.approx(11.25), 10.55)      # nothing else moved
    assert len(sent) == 2 and bold("LONG OUT") in sent[1] and "🛑 stop triggered" in sent[1]
    assert italic("Seen on a free-feed print of $10.15 at 10:04:20 ET, before its candle closed. A paper measurement on a "
                  "thin feed, not stop protection.") in sent[1].split("\n")
    for k in range(4, 12):                                                      # later passes: closed, never twice
        lb.tick(db, feed(DAY), sent.append, now=at(3, 30 * k), stop_print=falling)
    assert len(sent) == 2 and trades(db)[0]["est_exit"] == 10.15
    assert lb.stop_seen_line(db) == ("long stops since 11-09 (live paper estimate): 1 of 1 seen on a free-feed print before "
                                     "the candle closed, the rest when a candle closed. A measurement on a thin feed, not "
                                     "stop protection")


def test_with_no_print_at_the_stop_the_book_is_the_research_simulator_as_before(db):
    """Target and time exits stay on closed candles: with a reader that never shows the stop, every figure is the
    study's, exactly as without one."""
    alert_row(db)
    sent, calm = [], reader(10.9, BOOKED + pd.Timedelta(seconds=5))
    for k in range(0, 70 * 2):
        lb.tick(db, feed(DAY), sent.append, now=at(0, 30 * k), stop_print=calm)
    (t,) = trades(db)
    s = [x for x in ls.setups(DAY, {"posted_at": POSTED.isoformat(), "status": "live"})[0] if x.kind == "L4b"][0]
    ref = pt.simulate(DAY, s, latest=ls._latest("2026-11-09"))
    assert (t["status"], t["est_entry"], t["reason"]) == ("est_closed", ref.entry, ref.reason) and ref.reason == "target"
    assert t["est_exit"] == pytest.approx(ref.exit) and t["est_exit_at"] == ref.exit_ts.isoformat()
    assert t["stop_seen"] is None and "Seen on a free-feed print" not in sent[1] and lb.stop_seen_line(db) is None


def test_a_candle_that_closes_with_the_stop_wins_over_a_later_print_and_a_failing_reader_changes_nothing(db):
    alert_row(db)
    falls = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.65, 500),
                  (10.6, 10.62, 10.0, 10.1, 800)] + [(10.1, 10.2, 10.0, 10.1, 100)] * 50)

    def boom(symbol):
        raise RuntimeError("down")
    sent = []
    lb.tick(db, feed(falls), sent.append, now=at(3), stop_print=boom)           # booked; the reader is down
    assert trades(db)[0]["status"] == "open"
    lb.tick(db, feed(falls), sent.append, now=at(3, 30), stop_print=boom)       # between candles: nothing to act on
    assert trades(db)[0]["status"] == "open"
    lb.tick(db, feed(falls), sent.append, now=at(4), stop_print=reader(9.9, T1 + pd.Timedelta(minutes=3, seconds=50)))
    (t,) = trades(db)                                                           # the 15:04 candle closed with the stop
    assert (t["status"], t["reason"], t["est_exit"], t["stop_seen"]) == ("est_closed", "stop", 10.2, None)
    assert "Seen on a free-feed print" not in sent[-1]


def test_the_looks_of_one_exit_check_are_bounded_and_every_open_long_gets_its_turn(db, monkeypatch):
    """MAX_OPEN is 40 and each look is a request: an exit check makes at most TICK_LOOKS of them, the longs looked at
    longest ago first, so none is starved and the request rate does not grow with what is open."""
    monkeypatch.setattr(lb, "TICK_LOOKS", 2)
    lb._looked.clear()
    ids = []
    for sym in ("AAA", "BBB", "CCC", "DDD", "EEE"):
        fid = alert_row(db, sym)
        with db.tx() as c:
            ids.append(c.execute(
                "INSERT INTO long_trades(fade_id, symbol, session, kind, status, trigger, stop, target, weight, entry_ts, "
                "est_entry, created_at, rules) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (fid, sym, "2026-11-09", "L4b", "open", 10.55, 10.2, 11.25, 0.002, ENTRY_TS.isoformat(), 10.55,
                 BOOKED.isoformat(), lb.RULES)).lastrowid)
    calm, sent = reader(10.9, BOOKED + pd.Timedelta(seconds=5)), []
    day = closed(3)                                             # nothing final on closed candles: all five stay open
    for k in (1, 2, 3):
        lb._advance(db, lambda sym, now: day, sent.append, at(3, 10 * k), calm)
    # two a check, in turn, none skipped: the never-looked-at fifth before a second look at anyone but the oldest
    assert calm.calls[:4] == ["AAA", "BBB", "CCC", "DDD"] and sorted(calm.calls[4:]) == ["AAA", "EEE"]
    assert sent == [] and all(t["status"] == "open" for t in trades(db))
    with db.tx() as c:                                          # a long that has left the book leaves the turn list
        c.execute("UPDATE long_trades SET status='est_closed' WHERE id=?", (ids[1],))
    lb._advance(db, lambda sym, now: day, sent.append, at(3, 40), calm)
    assert calm.calls[-2:] == ["CCC", "DDD"] and ids[1] not in lb._looked
    lb._looked.clear()
