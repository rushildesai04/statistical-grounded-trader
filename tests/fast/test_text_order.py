"""A texted long is never cut: the order at the text is its official result (owner 2026-11-18)."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from sgt.fast import long_book as lb
from sgt.fast import patterns as pt
from sgt.fast import text_order as to
from sgt.ops.store import OpsStore

DAY, NIGHT = "2026-11-18", datetime(2026, 11, 19, 1, 40, tzinfo=timezone.utc)
TEXT = pd.Timestamp("2026-11-18 16:11:16.9", tz="UTC")           # the GMM fill text
M = pd.Timestamp("2026-11-18 16:11", tz="UTC")
COST = 0.01


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def bars(rows, start=M):
    return pd.DataFrame([{"ts": start + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": 100.0}
                         for i, (o, h, lo, c) in enumerate(rows)])


def prints(rows):
    return np.array([[(TEXT + pd.Timedelta(seconds=s)).value, p, 100.0] for s, p in rows], dtype=float).reshape(-1, 3)


def long(db, status="est_closed", reason=f"{lb.NO_FILL}; live exit target", stop=1.74, target=1.875, rules=lb.RULES, created=TEXT, fid=1, session=DAY):
    with db.tx() as c:
        return c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, trigger, stop, target, weight, entry_ts, "
                         "est_entry, est_exit, reason, created_at, rules) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                         (fid, "GMM", session, "L4b", status, 1.785, stop, target, 0.05, "2026-11-18T16:10:00+00:00", 1.785, 1.88,
                          reason, created.isoformat(), rules)).lastrowid


def row(db, rid=1):
    with db.tx() as c:
        return dict(c.execute("SELECT * FROM long_trades WHERE id=?", (rid,)).fetchone())


def run(db, pr, day, now=NIGHT):
    return to.run(db, lambda sym, a, b: pr, lambda sym, session: day, lambda sym, ts, px: COST, now=now)


FLAT = [(1.80, 1.81, 1.79, 1.80)] * 40


UNTIL = pd.Timestamp("2026-11-18 16:40", tz="UTC")              # the live fill bar 16:10 + the 30-minute hold


def test_result_walks_the_prints_of_the_minute_then_the_bars():
    r = lambda px, rest, day, red=False: to.result(px, TEXT, prints(rest), bars(day), 1.74, 1.875, red, UNTIL)  # noqa: E731
    assert r(1.74, [], FLAT) == {"how": "stop", "exit": 1.74, "exit_ts": TEXT}                    # bought into the stop
    assert r(1.90, [], FLAT) == {"how": "target", "exit": 1.90, "exit_ts": TEXT}                  # already past the target
    assert r(1.80, [], FLAT, True) == {"how": "red", "exit": 1.80, "exit_ts": TEXT}               # the live trade left red
    assert r(1.80, [(5, 1.82), (9, 1.88)], FLAT)["how"] == "target" and r(1.80, [(5, 1.88)], FLAT)["exit"] == 1.875
    got = r(1.80, [(5, 1.73)], FLAT)
    assert (got["how"], got["exit"]) == ("stop", 1.73) and got["exit_ts"] == TEXT + pd.Timedelta(seconds=5)
    up = [(1.80, 1.81, 1.79, 1.80), (1.80, 1.84, 1.80, 1.83), (1.86, 1.90, 1.85, 1.89)]
    assert (r(1.80, [], up)["how"], r(1.80, [], up)["exit"]) == ("target", 1.875)
    gap = [(1.80, 1.81, 1.79, 1.80), (1.70, 1.71, 1.60, 1.65)]
    assert (r(1.80, [], gap)["how"], r(1.80, [], gap)["exit"]) == ("stop", 1.70)                   # a gap under the stop
    drift = FLAT[:28] + [(1.80, 1.81, 1.79, 1.82)] + [(1.80, 1.95, 1.60, 1.90)] * 11              # 16:39 closes 1.82
    got = r(1.79, [], drift)
    assert (got["how"], got["exit"], got["exit_ts"]) == ("time", 1.82, UNTIL - pt.BAR)             # the live hold's end, not later
    dip = [(1.80, 1.81, 1.76, 1.77), (1.78, 1.79, 1.77, 1.78)]
    assert r(1.80, [], dip)["how"] == "time"              # a red minute after the text is NOT a new red exit
    assert r(1.80, [], dip[:1]) == {"how": "time", "exit": 1.77, "exit_ts": M}                     # no later bar: that close


def test_how_the_live_trade_left_is_kept_or_read_off_an_older_row():
    base = {"stop": 1.74, "target": 1.875, "entry_ts": "2026-11-18T16:10:00+00:00", "est_exit_at": "2026-11-18T16:16:00+00:00"}
    assert to.live_exit({**base, "reason": f"{lb.NO_FILL}; live exit red", "est_exit": 1.9}) == "red"
    assert to.live_exit({**base, "reason": lb.NO_FILL, "est_exit": 1.88}) == "target"             # GMM, voided before the fix
    assert to.live_exit({**base, "reason": lb.NO_FILL, "est_exit": 1.74}) == "stop"
    assert to.live_exit({**base, "reason": lb.NO_FILL, "est_exit": 1.80}) == "time"
    assert to.live_exit({**base, "reason": lb.NO_FILL, "est_exit": 1.80, "est_exit_at": "2026-11-18T16:11:00+00:00"}) == "red"
    assert to.live_exit({**base, "reason": f"{lb.NO_FILL}; live exit unknown", "est_exit": None}) == "time"


def test_the_gmm_long_is_scored_as_bought_at_the_text_and_sold_at_its_target(db):
    long(db)
    day = bars([(1.76, 1.80, 1.75, 1.79), (1.80, 1.84, 1.79, 1.83), (1.84, 1.89, 1.83, 1.88)] + FLAT)
    got = run(db, prints([(0.4, 1.79), (20, 1.80)]), day)
    t = row(db)
    assert got == [f"GMM L4b {1.875 / 1.79 - 1 - COST:+.2%} (bought at the text $1.79; target)"]
    assert (t["status"], t["reason"], t["entry"], t["exit"], t["exit_reason"]) == ("settled", to.REASON, 1.79, 1.875, "target")
    assert t["net"] == pytest.approx(1.875 / 1.79 - 1 - COST) and t["cost_rt"] == COST and t["settled_at"] is not None
    assert pd.Timestamp(t["fill_ts"]) == TEXT + pd.Timedelta(seconds=0.4) and pd.Timestamp(t["exit_ts"]) == M + 2 * pt.BAR
    assert run(db, prints([(0.4, 1.60)]), day) == [] and row(db)["entry"] == 1.79                  # booked once
    assert lb.nav(db) > 100_000


def test_no_print_soon_after_the_text_buys_the_next_bars_open(db):
    long(db)
    day = bars([(1.76, 1.80, 1.75, 1.79), (1.81, 1.82, 1.80, 1.81)] + FLAT)
    late = prints([(200, 1.85)])                                                   # the first print is over 2 minutes later
    assert len(run(db, late, day)) == 1
    t = row(db)
    assert (t["status"], t["entry"]) == ("settled", 1.81) and pd.Timestamp(t["fill_ts"]) == M + pt.BAR


def test_only_a_tape_with_no_trade_after_the_text_is_void(db):
    long(db)
    before = bars([(1.76, 1.80, 1.75, 1.79)], start=M - pt.BAR)                    # the tape ends before the text
    assert run(db, to.NO_PRINTS, before) == [f"GMM L4b void ({to.IMPOSSIBLE})"]
    assert (row(db)["status"], row(db)["reason"]) == ("void", to.IMPOSSIBLE)


def test_failed_requests_wait_and_stale_days_end_on_the_bars(db):
    long(db)
    day = bars(FLAT)
    assert run(db, None, day) == [] and row(db)["status"] == "est_closed"          # prints failed: tomorrow
    assert run(db, prints([(1, 1.8)]), pd.DataFrame()) == [] and row(db)["status"] == "est_closed"   # bars not there yet
    assert run(db, prints([(1, 1.8)]), day, now=datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)) == []   # before the close
    week = datetime(2026, 11, 25, 1, 40, tzinfo=timezone.utc)
    assert len(run(db, None, day, now=week)) == 1 and row(db)["entry"] == 1.80     # prints never came: the next bar's open
    long(db, fid=2)
    assert run(db, None, pd.DataFrame(), now=week) == ["GMM L4b void (no official SIP bars)"]


def test_only_longs_waiting_for_it_are_touched(db):
    a = long(db, status="settled", reason="time", fid=1)
    b = long(db, status="est_closed", reason="target", fid=2)                      # still to be scored the tested way
    c = long(db, status="void", reason=lb.NO_FILL, fid=3)                          # an old void is not restated here
    assert len(run(db, prints([(1, 1.8)]), bars(FLAT))) == 0
    d = long(db, status=lb.SEEN, reason=lb.NO_FILL, fid=4)
    assert run(db, prints([(1, 1.8)]), bars(FLAT)) == []
    assert [row(db, i)["status"] for i in (a, b, c, d)] == ["settled", "est_closed", "void", lb.SEEN]


def test_a_long_that_left_red_live_is_bought_and_sold_at_once(db):
    long(db, reason=f"{lb.NO_FILL}; live exit red")
    run(db, prints([(1, 1.80)]), bars(FLAT))
    t = row(db)
    assert (t["exit_reason"], t["entry"], t["exit"]) == ("red", 1.80, 1.80) and t["net"] == pytest.approx(-COST)
