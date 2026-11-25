"""A list read WITHOUT the write lock, then acted on (2026-11-24: four loops read their lists with ``db.read()``).

Between the read and the write another task can close, correct or settle the same position. The write is what keeps
the state whole: every state change is one guarded statement (``... WHERE id=? AND status=?``), so a stale read can
lose the race but can never close a position twice, reopen a settled one, or send a second, contradictory text.
Each test lets "another task" act from inside the market-data call, exactly between the read and the write."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from sgt.fast import alert_context, fade, long_book, scanner, tape_check
from sgt.fast.fade_rule import VETO
from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore, iso

ET = ZoneInfo("America/New_York")
IN = datetime(2026, 11, 17, 10, 1, 9, tzinfo=ET).astimezone(timezone.utc)


@pytest.fixture()
def db(tmp_path):
    d = OpsStore(tmp_path / "ops.db")
    ap.arm(d, IN)
    return d


def short(db, status="open", sym="MPU", entry=1.54, mid=1):
    with db.tx() as c:
        return c.execute(
            "INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, weight,"
            " est_entry, est_entry_at, est_last, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, "2026-11-17", "gaining_momentum", iso(IN - timedelta(seconds=69)), "-1", mid, status, VETO, 0.0125,
             entry, iso(IN), entry, iso(IN))).lastrowid


def one(db, table, rid):
    with db.read() as c:
        return dict(c.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone())  # noqa: S608 - fixed names


def closes(db, book, rid):
    return [d for d in db.decisions(book, rid) if d["state_to"] in ("est_closed", "settled")]


def test_the_short_monitor_does_not_close_or_text_a_position_another_task_closed_first(db):
    rid, sent = short(db), []

    def quote(symbol, **k):                         # between the monitor's read and its write: the official-tape check
        with db.tx() as c:                          # closes the short at the stop it saw (its own guarded statement)
            c.execute("UPDATE fade_trades SET status='est_closed', reason=?, est_exit=1.848, est_exit_at=? WHERE id=? "
                      "AND status='open'", (tape_check.LATE, iso(IN + timedelta(minutes=30)), rid))
        return 1.95                                 # through the monitor's stop as well

    fade.monitor(db, sent.append, quote, now=IN + timedelta(minutes=31))
    r = one(db, "fade_trades", rid)
    assert (r["status"], r["reason"], r["est_exit"]) == ("est_closed", tape_check.LATE, 1.848)   # the first close stands
    assert sent == [] and closes(db, "short", rid) == []         # no second close on record, no second text


def test_the_official_tape_check_does_not_correct_a_trade_that_settled_in_between(db):
    rid, sent = short(db), []
    cross = pd.DataFrame([{"ts": pd.Timestamp(IN).floor("min") + pd.Timedelta(minutes=5), "open": 1.60, "high": 2.50}])

    def bars(symbol, start, end):                   # the nightly settle books the trade while the check holds its list
        with db.tx() as c:
            c.execute("UPDATE fade_trades SET status='settled', entry=1.5, cover=1.55, net=-0.045, settled_at=? "
                      "WHERE id=?", (iso(IN + timedelta(hours=10)), rid))
        return cross

    assert tape_check.check(db, sent.append, bars, now=IN + timedelta(minutes=40)) == 0
    r = one(db, "fade_trades", rid)
    assert (r["status"], r["est_exit"], r["net"]) == ("settled", None, -0.045) and sent == []


def test_a_settle_leaves_the_live_estimate_as_it_was_and_the_monitor_never_reopens_or_closes_it(db):
    """The replay can end (even stop out) while the live paper path is still open: the settle writes only the settled
    columns, the live ones stay as they were, and nothing texts a close for a settled row afterwards."""
    rid, sent = short(db), []
    res = pd.DataFrame([{"id": rid, "qualifies": True, "why": "", "entry_ts": "2026-11-17 15:02:00+00:00", "entry": 1.5,
                         "cover": 1.8, "cost_rt": 0.01, "gross": -0.2, "net": -0.215, "rule_entry": 1.5,
                         "rule_net": -0.215}])
    assert fade.settle(db, lambda due: res, now=IN + timedelta(hours=10)) == ["MPU -21.50%"]
    r = one(db, "fade_trades", rid)
    assert (r["status"], r["reason"], r["est_entry"], r["est_exit"], r["net"]) == ("settled", VETO, 1.54, None, -0.215)
    assert fade.settled_exit(r) == "stop"                        # the replay's own exit, read off its numbers
    fade.monitor(db, sent.append, lambda s, **k: 2.5, now=IN + timedelta(hours=10, seconds=20))
    assert one(db, "fade_trades", rid)["status"] == "settled" and sent == []
    assert fade.settle(db, lambda due: res, now=IN + timedelta(hours=11)) == []      # a second run settles nothing twice
    assert [d["state_to"] for d in closes(db, "short", rid)] == ["settled"]


def test_the_long_book_does_not_close_or_text_a_long_another_task_closed_first(db):
    fid = short(db, status="filtered", sym="LNG")
    entry_ts = pd.Timestamp("2026-11-17 15:10:00+00:00")
    with db.tx() as c:
        lid = c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, target, weight, entry_ts, "
                        "est_entry, est_last, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (fid, "LNG", "2026-11-17", "L8", "open", 9.5, 11.0, 0.005, entry_ts.isoformat(), 10.0, 10.0,
                         iso(IN))).lastrowid
    f = pd.DataFrame([{"ts": entry_ts + pd.Timedelta(minutes=k), "open": 10.0, "high": 10.1, "low": 9.0 if k == 2 else 9.9,
                       "close": 9.95, "volume": 1000} for k in range(4)])
    sent = []

    def bars(symbol, now):                          # another task (the settle's catch-up) closes it in between
        with db.tx() as c:
            c.execute("UPDATE long_trades SET status='est_closed', reason='closed elsewhere', est_exit=9.8 WHERE id=?", (lid,))
        return f

    long_book._advance(db, bars, sent.append, datetime(2026, 11, 17, 15, 20, tzinfo=timezone.utc))
    r = one(db, "long_trades", lid)
    assert (r["status"], r["reason"], r["est_exit"]) == ("est_closed", "closed elsewhere", 9.8) and sent == []
    sent.clear()                                    # the control: left alone, the same bars DO close it at the stop
    with db.tx() as c:
        c.execute("UPDATE long_trades SET status='open', reason=NULL, est_exit=NULL WHERE id=?", (lid,))
    long_book._advance(db, lambda s, n: f, sent.append, datetime(2026, 11, 17, 15, 20, tzinfo=timezone.utc))
    assert one(db, "long_trades", lid)["status"] == "est_closed" and len(sent) == 1


def test_the_scanner_does_not_close_a_row_twice(db, monkeypatch):
    monkeypatch.setattr(scanner, "ACTIVE", True, raising=False)
    with db.tx() as c:
        sid = c.execute("INSERT INTO scan_trades(symbol, session, bar_ts, detected_at, signal_px, status, weight, "
                        "est_entry, est_entry_at, est_last, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        ("SCN", "2026-11-17", iso(IN - timedelta(minutes=1)), iso(IN), 5.0, "open", 0.005, 5.0, iso(IN),
                         5.0, iso(IN))).lastrowid
    sent = []

    def quote(symbol, **k):
        with db.tx() as c:
            c.execute("UPDATE scan_trades SET status='est_closed', reason='60 min', est_exit=4.9 WHERE id=?", (sid,))
        return 6.5                                  # through the stop

    scanner.step(db, quote, IN + timedelta(minutes=10), sent.append)
    r = one(db, "scan_trades", sid)
    assert (r["status"], r["reason"], r["est_exit"]) == ("est_closed", "60 min", 4.9) and sent == []


def test_the_context_pass_does_not_overwrite_a_stamp_another_path_wrote_first(db):
    rid = short(db, status="pending", sym="CTX")
    now = IN + timedelta(seconds=5)

    def gather(jobs, budget):                       # another path stamps the row while this pass is reading its sources
        with db.tx() as c:
            c.execute("UPDATE fade_trades SET ctx_at=?, ctx_note='first' WHERE id=?", (iso(now), rid))
        return {k: job() for k, job in jobs.items()}

    src = alert_context.SOURCES[0]
    assert [r["id"] for r in alert_context.due(db, now)] == [rid]
    assert alert_context.stamp(db, {src: lambda s, t: {"own": 1, "kind": "OTHER"}}, gather, now) == 0
    assert one(db, "fade_trades", rid)["ctx_note"] == "first"
    with db.tx() as c:                              # the control: with nobody in between, the same pass does stamp
        c.execute("UPDATE fade_trades SET ctx_at=NULL, ctx_note=NULL WHERE id=?", (rid,))
    assert alert_context.stamp(db, {src: lambda s, t: {"own": 1, "kind": "OTHER"}},
                               lambda jobs, budget: {k: job() for k, job in jobs.items()}, now) == 1
