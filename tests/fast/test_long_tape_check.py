"""Official-tape stop check for longs (owner 2026-11-21, 2 C): a late correction, never stop protection."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from sgt.fast import est_costs
from sgt.fast import long_tape_check as lt
from sgt.fast.tape_check import LATE
from sgt.ops.sms_style import bold
from sgt.ops.store import OpsStore, iso

IN = datetime(2026, 11, 12, 15, 3, 0, tzinfo=timezone.utc)         # 10:03 ET: bought 5.30, stop 5.14, target 5.62
ENTRY, STOP, TARGET = 5.30, 5.14, 5.62


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def long(db, status="open", reason=None, exit_at=None, exit_px=None, sym="ADXN", fid=1, kind="L4b"):
    with db.tx() as c:
        return c.execute(
            "INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, target, weight, entry_ts, est_entry, "
            "est_last, est_exit, est_exit_at, reason, created_at, rules) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, sym, "2026-11-12", kind, status, STOP, TARGET, 0.005, iso(IN), ENTRY, exit_px or ENTRY, exit_px,
             None if exit_at is None else iso(exit_at), reason, iso(IN + timedelta(seconds=70)), "A2")).lastrowid


def row(db, rid):
    with db.tx() as c:
        return dict(c.execute("SELECT * FROM long_trades WHERE id=?", (rid,)).fetchone())


def tape(*bars_):
    """Official bars as (HH:MM ET, open, high, low)."""
    f = pd.DataFrame([{"ts": pd.Timestamp(f"2026-11-12 {hm}", tz="America/New_York").tz_convert("UTC"), "open": o,
                       "high": h, "low": lo} for hm, o, h, lo in bars_])
    calls = []

    def bars(symbol, start, end):
        calls.append((symbol, start, end))
        return f[(f.ts >= pd.Timestamp(start)) & (f.ts < pd.Timestamp(end))] if len(f) else f
    bars.calls = calls
    return bars


LATER = IN + timedelta(minutes=40)


def test_an_open_long_whose_stop_the_tape_crossed_is_closed_there_with_its_exit_text(db):
    rid, sent = long(db), []
    n = lt.check(db, sent.append, tape(("10:03", 5.30, 5.35, 5.28), ("10:05", 5.20, 5.22, 5.10)), now=LATER)
    r = row(db, rid)
    assert n == 1 and (r["status"], r["reason"], r["est_exit"]) == ("est_closed", LATE, STOP)
    assert pd.Timestamp(r["est_exit_at"]) == pd.Timestamp("2026-11-12 10:05", tz="America/New_York")
    assert len(sent) == 1 and bold("LONG OUT") in sent[0] and "stop triggered" in sent[0] and "A stop is a trigger, not a cap." in sent[0] and "capped" not in sent[0]
    assert "Seen late: the stop traded at 10:05 ET on the official tape" in sent[0]
    assert lt.check(db, sent.append, tape(("10:05", 5.20, 5.22, 5.10)), now=LATER) == 0 and len(sent) == 1   # once


def test_a_long_texted_as_target_is_corrected_once_when_the_tape_stopped_it_first(db):
    # ADXN 2026-11-12: the free feed showed the target at 10:09; the official tape had traded under the stop at 10:04
    rid, sent = long(db, "est_closed", "target", IN + timedelta(minutes=6), TARGET), []
    bars = tape(("10:03", 5.30, 5.33, 5.25), ("10:04", 5.20, 5.21, 5.12), ("10:09", 5.5, 5.7, 5.5))
    assert lt.check(db, sent.append, bars, now=LATER) == 1
    r = row(db, rid)
    assert (r["reason"], r["est_exit"]) == (LATE, STOP)
    cost = est_costs.cost("long", "ADXN", iso(IN), ENTRY) or 0.0
    assert len(sent) == 1 and sent[0].startswith("[SGT] ✏️ CORRECTION · ADXN long: the stop traded at 10:04 ET")
    assert f"Result: {STOP / ENTRY - 1 - cost:+.1%}" in sent[0] and f"not the {TARGET / ENTRY - 1 - cost:+.1%} texted earlier" in sent[0]
    assert "(we sold $5.62 later: target)" in sent[0] and "not stop\nprotection" not in sent[0] and "not stop protection" in sent[0]
    assert lt.check(db, sent.append, bars, now=LATER) == 0 and len(sent) == 1


def test_a_gap_under_the_stop_sells_at_the_minutes_open(db):
    rid = long(db)
    lt.check(db, [].append, tape(("10:06", 4.90, 4.95, 4.80)), now=LATER)
    assert row(db, rid)["est_exit"] == 4.90


def test_the_entry_minute_itself_counts_and_in_one_minute_the_stop_comes_first(db):
    rid = long(db)
    lt.check(db, [].append, tape(("10:03", 5.30, 5.70, 5.10)), now=LATER)       # target and stop in the buy minute
    assert row(db, rid)["reason"] == LATE


def test_a_target_reached_on_the_tape_in_an_earlier_minute_is_not_a_stop(db):
    rid, sent = long(db, "est_closed", "target", IN + timedelta(minutes=10), TARGET), []
    bars = tape(("10:04", 5.40, 5.65, 5.38), ("10:07", 5.30, 5.31, 5.00))        # target at 10:04, the fall came later
    assert lt.check(db, sent.append, bars, now=LATER) == 0 and sent == [] and row(db, rid)["reason"] == "target"


def test_only_minutes_before_the_live_exit_and_old_enough_to_be_official_are_read(db):
    rid, sent = long(db, "est_closed", "time", IN + timedelta(minutes=30), 5.25), []
    bars = tape(("10:33", 5.2, 5.2, 5.0), ("10:40", 5.2, 5.2, 5.0))               # the stop traded AFTER our sale
    assert lt.check(db, sent.append, bars, now=LATER) == 0 and row(db, rid)["reason"] == "time"
    fresh, t = long(db, sym="NEW", fid=2), tape(("10:05", 5.2, 5.2, 5.0))
    only_new = lambda sym, a, b: t(sym, a, b) if sym == "NEW" else None  # noqa: E731
    just = IN + timedelta(minutes=10)                                              # official bars are 16 minutes late
    assert lt.check(db, sent.append, only_new, now=just) == 0 and row(db, fresh)["status"] == "open"
    assert lt.check(db, sent.append, only_new, now=IN + timedelta(minutes=22)) == 1


def test_a_live_stop_a_settled_trade_and_an_old_exit_are_left_alone(db):
    a = long(db, "est_closed", "stop", IN + timedelta(minutes=5), STOP)
    b = long(db, "settled", "target", IN + timedelta(minutes=6), TARGET, sym="B", fid=2)
    c = long(db, "est_closed", "target", IN + timedelta(minutes=6), TARGET, sym="C", fid=3)
    bars = tape(("10:04", 5.2, 5.2, 5.0))
    assert lt.check(db, [].append, bars, now=IN + timedelta(hours=3)) == 0          # c: its recheck window is over
    assert [row(db, x)["reason"] for x in (a, b, c)] == ["stop", "target", "target"]


def test_malformed_bars_and_a_failing_source_never_raise_or_change_a_trade(db):
    rid, sent = long(db), []
    bad = lambda s, a, b: pd.DataFrame({"ts": ["x", None], "open": ["a", 1], "high": [1, 1], "low": [None, -5]})  # noqa: E731
    assert lt.check(db, sent.append, bad, now=LATER) == 0
    assert lt.check(db, sent.append, lambda s, a, b: (_ for _ in ()).throw(RuntimeError("down")), now=LATER) == 0
    assert lt.check(db, sent.append, lambda s, a, b: None, now=LATER) == 0
    assert row(db, rid)["status"] == "open" and sent == []


def test_first_stop_reads_only_sane_rows():
    assert lt.first_stop(None, IN, LATER, STOP, TARGET) is None
    assert lt.first_stop(pd.DataFrame({"ts": [IN], "open": [5.3]}), IN, LATER, STOP, TARGET) is None      # no low column
    f = pd.DataFrame({"ts": [IN + timedelta(minutes=1)], "open": [5.2], "high": [5.2], "low": [5.14]})
    assert lt.first_stop(f, IN, LATER, STOP, TARGET) == ((IN + timedelta(minutes=1)), STOP)             # at the stop counts
    assert lt.first_stop(f, IN, LATER, STOP - 0.01, TARGET) is None
