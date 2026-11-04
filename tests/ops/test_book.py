"""Approved book (src/sgt/ops/book.py): exits automatic, entries need approval, close-to-close marking."""
from __future__ import annotations

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
