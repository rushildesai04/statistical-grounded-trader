"""Approved book (src/sgt/ops/book.py): exits automatic, entries need approval, close-to-close marking."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from sgt.ops import approvals as ap
from sgt.ops import book
from sgt.ops.store import OpsStore

T0 = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)
D = [date(2026, 11, 2), date(2026, 11, 3), date(2026, 11, 4), date(2026, 11, 5)]


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


class Outbox(list):
    def __call__(self, text):
        self.append(text)


def closes(table: dict[str, list[float]]):
    frame = pd.DataFrame(table, index=D)

    def fn(symbols, start, end):
        return frame[[s for s in symbols if s in frame]].loc[(frame.index >= start) & (frame.index <= end)]
    return fn


def test_split_rules():
    auto, ask = book.split({"A": 0.2, "B": 0.2, "C": -0.1, "D": 0.1},
                           {"A": 0.1, "B": 0.3, "C": 0.1, "E": 0.05})
    assert auto == {"A": 0.1, "C": 0.0, "D": 0.0}      # reduce, flip-exit, exit
    assert ask == {"B": 0.3, "C": 0.1, "E": 0.05}      # increase, flip-entry, new


def test_propose_approve_settle_marks_nav(db):
    out = Outbox()
    res = book.propose(db, out, strategy="m", targets={"A": 0.5}, fill_session=D[1],
                       deadline=T0 + timedelta(days=1), label="m 11-02", now=T0)
    assert res["ask"] == {"A": 0.5} and "OK" in out[-1]
    code = re.findall(r"OK (\d{6}|\d{2})\b", out[-1])[0]
    _, row = ap.answer(db, ap.Command("OK", code), "SM1", now=T0)
    assert book.apply_approved(db, row, now=T0).startswith("scheduled")
    assert book.apply_approved(db, row, now=T0) == "already handled"
    px = closes({"A": [100, 100, 110, 99]})
    fills, problems = book.settle(db, px, D[2])
    assert problems == [] and [f["session"] for f in fills] == ["2026-11-03"]
    with db.tx() as c:
        navs = [r["nav"] for r in c.execute("SELECT nav FROM book_nav ORDER BY session")]
    entry = book.NAV0 * (1 - 0.5 * book.COST_BP / 1e4)
    assert navs == pytest.approx([entry, entry * (1 + 0.5 * 0.10)])
    # exit is automatic: no approval text, scheduled immediately
    n = len(out)
    res = book.propose(db, out, strategy="m", targets={"A": 0.0}, fill_session=D[3],
                       deadline=T0 + timedelta(days=3), label="m 11-04", now=T0)
    assert res["ask"] == {} and res["approval_id"] is None and "auto" in out[n]
    fills, _ = book.settle(db, px, D[3])
    assert fills[0]["target"] == 0.0 and book.holdings(db, "m")[0] == {}


def test_unapproved_entry_never_fills_and_missing_close_stops(db):
    out = Outbox()
    book.propose(db, out, strategy="m", targets={"A": 0.5}, fill_session=D[1],
                 deadline=T0 + timedelta(hours=1), label="m", now=T0)
    ap.expire(db, T0 + timedelta(hours=2))
    assert book.settle(db, closes({"A": [1, 1, 1, 1]}), D[3]) == ([], [])
    with db.tx() as c:
        c.execute("INSERT INTO book_orders(strategy, symbol, target, fill_session, source, status, created_at) "
                  "VALUES ('z','GONE',0.1,'2026-11-03','approved','scheduled','x')")
    fills, problems = book.settle(db, closes({"A": [1, 1, 1, 1]}), D[3])
    assert fills == [] and "missing close" in problems[0]


def test_targets_are_bounded(db):
    with pytest.raises(ValueError):
        book.propose(db, Outbox(), strategy="m", targets={"A": 1.5}, fill_session=D[1],
                     deadline=T0 + timedelta(days=1), label="m", now=T0)


def test_auto_all_schedules_entries_without_an_approval(db):
    out = []
    res = book.propose(db, out.append, strategy="auto", targets={"A": 0.5, "B": -0.2}, fill_session=D[1],
                       deadline=datetime(2099, 1, 1, tzinfo=timezone.utc), label="auto", auto_all=True)
    assert res["ask"] == {} and res["approval_id"] is None and res["auto"] == {"A": 0.5, "B": -0.2}
    assert len(out) == 1 and "auto at" in out[0]


def _two_stock_book(db):
    """A book run through settle: A and B held, A resized on D[2], B closed on D[3]."""
    px = closes({"A": [100.0, 110.0, 99.0, 104.0], "B": [50.0, 45.0, 54.0, 51.0]})
    far = datetime(2099, 1, 1, tzinfo=timezone.utc)
    for d, targets in ((D[0], {"A": 0.5, "B": -0.3}), (D[2], {"A": 0.2, "B": -0.3}), (D[3], {"A": 0.2})):
        book.propose(db, Outbox(), strategy="m", targets=targets, fill_session=d, deadline=far, label="m", auto_all=True)
        assert book.settle(db, px, d)[1] == []
    return px


def _legs(db):
    with db.tx() as c:
        return [tuple(r) for r in c.execute("SELECT strategy, session, symbol, weight, usd, close FROM book_legs "
                                            "ORDER BY session, symbol")]


def test_settle_books_each_held_symbols_dollars_and_they_explain_the_mark(db):
    _two_stock_book(db)
    with db.tx() as c:
        marks = [dict(r) for r in c.execute("SELECT session, nav, ret, holdings FROM book_nav ORDER BY session")]
    legs = _legs(db)
    v0 = marks[0]["nav"]                                              # after the entry's cost
    assert [x[1:4] for x in legs] == [                                # the weights held INTO each session, none on entry day
        ("2026-11-03", "A", 0.5), ("2026-11-03", "B", -0.3), ("2026-11-04", "A", 0.5), ("2026-11-04", "B", -0.3),
        ("2026-11-05", "A", 0.2), ("2026-11-05", "B", -0.3)]
    assert legs[0][4:] == pytest.approx((0.5 * v0 * 0.10, 110.0)) and legs[1][4:] == pytest.approx((-0.3 * v0 * -0.10, 45.0))
    for i in (1, 2, 3):                                               # every mark = value before + its rows - its cost
        before, row = marks[i - 1], marks[i]
        was, now = json.loads(before["holdings"]), json.loads(row["holdings"])
        turnover = sum(abs(now.get(s, 0) - was.get(s, 0)) for s in set(now) | set(was))
        booked = sum(x[4] for x in legs if x[1] == row["session"])
        assert row["nav"] == pytest.approx(before["nav"] + booked - turnover * before["nav"] * book.COST_BP / 1e4, abs=1e-6)
    assert marks[2]["nav"] != pytest.approx(marks[1]["nav"]) and len(legs) == 6


def test_backfill_rebuilds_the_rows_settle_would_have_written_and_is_idempotent(db):
    px = _two_stock_book(db)
    want = _legs(db)
    with db.tx() as c:
        c.execute("DELETE FROM book_legs WHERE session<'2026-11-05'")   # marked before the table existed
    assert book.backfill_legs(db, px) == (4, [])
    got = _legs(db)
    assert [x[:4] for x in got] == [x[:4] for x in want]
    assert [x[4:] for x in got] == [pytest.approx(x[4:], abs=1e-6) for x in want]
    calls = []

    def counting(symbols, start, end):
        calls.append(symbols)
        return px(symbols, start, end)
    assert book.backfill_legs(db, counting) == (0, []) and calls == []    # nothing missing: no rows, no request


def test_backfill_writes_nothing_for_a_session_it_cannot_reproduce(db):
    _two_stock_book(db)
    want = _legs(db)
    with db.tx() as c:
        c.execute("DELETE FROM book_legs")
    moved = closes({"A": [100.0, 110.0, 99.5, 104.0], "B": [50.0, 45.0, 54.0, 51.0]})   # one close restated since
    n, problems = book.backfill_legs(db, moved)
    assert n == 2 and [x[1] for x in _legs(db)] == ["2026-11-03", "2026-11-03"]        # the sessions that still replay
    assert len(problems) == 2 and all("does not reproduce the book" in p for p in problems)
    assert "2026-11-04" in problems[0] and "2026-11-05" in problems[1]
    assert "($" in problems[0]                                                          # how far off, in dollars
    # a dividend restates the earlier adjusted closes by a hair (rounded): still the book, within a dollar a session
    with db.tx() as c:
        c.execute("DELETE FROM book_legs")
    paid = closes({"A": [99.9240, 109.9164, 98.9248, 103.9210], "B": [50.0, 45.0, 54.0, 51.0]})   # x 0.99924, 4 decimals
    assert book.backfill_legs(db, paid) == (6, [])
    diffs = [abs(got[4] - exact[4]) for got, exact in zip(_legs(db), want)]
    assert max(diffs) < book.LEGS_TOL and max(diffs) > 1e-6
    with db.tx() as c:
        c.execute("DELETE FROM book_legs")
    # the line is a dollar a session: $1.96 off is refused, under a dollar off on the next session is taken
    near = closes({"A": [100.0, 110.0, 99.004, 104.0], "B": [50.0, 45.0, 54.0, 51.0]})
    n, problems = book.backfill_legs(db, near)
    assert n == 4 and len(problems) == 1 and "2026-11-04" in problems[0] and "($+1.96)" in problems[0]
    with db.tx() as c:
        c.execute("DELETE FROM book_legs")
    gone = closes({"B": [50.0, 45.0, 54.0, 51.0]})                                      # a symbol with no closes at all
    n, problems = book.backfill_legs(db, gone)
    assert n == 0 and len(problems) == 3 and all("no closes to replay" in p for p in problems)
