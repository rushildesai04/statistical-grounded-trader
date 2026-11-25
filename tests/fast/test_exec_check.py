"""Execution check (audit 2026-11-21): the official record against what the bot could have done. A measurement."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from sgt.fast import exec_check as ec
from sgt.fast.est_costs import LOCATE
from sgt.ops.store import OpsStore

DAY, NIGHT = "2026-11-18", datetime(2026, 11, 19, 1, 40, tzinfo=timezone.utc)
OPEN = pd.Timestamp("2026-11-18 14:30", tz="UTC")                # 09:30 ET
COST = 0.01


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def rth(path=None, flat=10.0):
    """Official regular-hours minutes; ``path``: minute-of-session -> (open, high, low, close)."""
    path, rows = path or {}, []
    for m in range(390):
        o, h, lo, c = path.get(m, (flat, flat, flat, flat))
        rows.append({"ts": OPEN + pd.Timedelta(minutes=m), "segment": "rth", "open": o, "high": h, "low": lo, "close": c,
                     "volume": 100.0})
    return pd.DataFrame(rows)


def short(db, sym="AAA", posted="2026-11-18T15:00:20+00:00", got="2026-11-18T15:00:40+00:00",
          decided="2026-11-18T15:01:12+00:00", entry_ts="2026-11-18 15:01:00+00:00", net=0.05, status="settled",
          session=DAY, mid=[0]):
    mid[0] += 1
    with db.tx() as c:
        return c.execute(
            "INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, created_at,"
            " est_entry, est_entry_at, entry_ts, entry, net) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, session, "volume_spike", posted, "-1", mid[0], status, "60 min", got, 10.0, decided, entry_ts, 10.0,
             net)).lastrowid


def long(db, fade_id, sym="AAA", exit_ts="2026-11-18 15:10:00+00:00", reason="stop", entry=10.0, stop=9.7, net=-0.04,
         status="settled", session=DAY):
    with db.tx() as c:
        return c.execute(
            "INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, entry, exit, exit_ts, exit_reason, net, "
            "cost_rt, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fade_id, sym, session, "L8", status, stop, entry, stop, exit_ts, reason, net, COST, "x")).lastrowid


def one(db, table, rid):
    with db.tx() as c:
        return dict(c.execute(f"SELECT * FROM {table} WHERE id=?", (rid,)).fetchone())  # noqa: S608 - test, fixed names


def run(db, days, now=NIGHT):
    return ec.run(db, lambda sym, session: days.get(sym), lambda sym, ts, px: COST, now=now)


def test_short_is_scored_from_the_first_minute_at_or_after_the_decision(db):
    # decided 15:01:12 -> the 15:02 minute (minute 32) opens 11; it then falls to 9.9 by the 60-minute cover
    bars = rth({32: (11.0, 11.0, 11.0, 11.0), 91: (9.9, 9.9, 9.9, 9.9)})
    rid = short(db)
    assert run(db, {"AAA": bars}) == ["AAA short at the decision +8.5%"]
    r = one(db, "fade_trades", rid)
    assert r["dec_entry"] == 11.0
    assert r["dec_net"] == pytest.approx((11.0 - 9.9) / 11.0 - COST - LOCATE)
    assert r["dec_note"] is None and r["net"] == 0.05           # the official result is never touched


def test_a_decision_exactly_on_the_minute_uses_that_minute(db):
    rid = short(db, decided="2026-11-18T15:02:00+00:00")
    run(db, {"AAA": rth({32: (11.0, 11.0, 11.0, 11.0)})})
    assert one(db, "fade_trades", rid)["dec_entry"] == 11.0


def test_the_stop_applies_from_the_decision_entry(db):
    # sold 10 at 15:02; a later minute trades 12.5: the 20% stop (12.0) covers there
    rid = short(db)
    run(db, {"AAA": rth({40: (10.0, 12.5, 10.0, 12.4)})})
    assert one(db, "fade_trades", rid)["dec_net"] == pytest.approx(-0.20 - COST - LOCATE)


def test_no_minute_after_the_decision_is_a_note_not_a_number(db):
    rid = short(db, decided="2026-11-18T21:05:00+00:00")       # after the close
    assert run(db, {"AAA": rth()}) == ["AAA short: no minute"]
    r = one(db, "fade_trades", rid)
    assert r["dec_net"] is None and r["dec_note"] == ec.NO_MINUTE


def test_written_once_and_missing_bars_are_retried(db):
    rid = short(db)
    assert run(db, {}) == []                                    # no bars yet: nothing written, retried
    assert one(db, "fade_trades", rid)["dec_net"] is None and one(db, "fade_trades", rid)["dec_note"] is None
    assert len(run(db, {"AAA": rth()})) == 1
    first = one(db, "fade_trades", rid)["dec_net"]
    assert run(db, {"AAA": rth({32: (20.0, 20.0, 20.0, 20.0)})}) == []     # a second run never rewrites it
    assert one(db, "fade_trades", rid)["dec_net"] == first


def test_only_settled_shorts_inside_the_lookback(db):
    a = short(db, sym="OPENED", status="est_closed")
    b = short(db, sym="OLD", session="2026-10-01")
    run(db, {"OPENED": rth(), "OLD": rth()})
    assert one(db, "fade_trades", a)["dec_net"] is None and one(db, "fade_trades", b)["dec_net"] is None


def test_malformed_bars_never_raise_or_write(db):
    rid = short(db)
    assert run(db, {"AAA": pd.DataFrame({"ts": ["not a time"], "open": ["x"]})}) == []
    assert one(db, "fade_trades", rid)["dec_net"] is None


def test_long_stop_exit_records_that_minutes_low(db):
    fid = short(db)
    lid = long(db, fid)                                        # stop exit in the 15:10 minute (minute 40)
    out = run(db, {"AAA": rth({40: (9.9, 9.9, 9.3, 9.4)})})
    assert "AAA long stop minute low 9.3" in out
    assert one(db, "long_trades", lid)["stop_low"] == 9.3
    assert one(db, "long_trades", lid)["net"] == -0.04          # the official result is never touched


def test_long_target_exits_and_open_longs_are_left_alone(db):
    a = long(db, short(db, sym="T"), sym="T", reason="target")
    b = long(db, short(db, sym="O"), sym="O", status="est_closed")
    run(db, {"T": rth(), "O": rth()})
    assert one(db, "long_trades", a)["stop_low"] is None and one(db, "long_trades", b)["stop_low"] is None


def test_summary_and_line(db):
    assert ec.line(db) is None                                 # nothing recorded: no line
    short(db, sym="LATE", got="2026-11-18T15:01:05+00:00", net=0.10)       # received 5 s after the official minute opened
    fid = short(db, sym="INTIME", net=-0.20)
    short(db, sym="ALSO", net=-0.05)                                       # received 20 s before its official minute
    long(db, fid, sym="INTIME", entry=10.0, stop=9.7, net=-0.04)
    run(db, {"LATE": rth(), "ALSO": rth(), "INTIME": rth({40: (9.9, 9.9, 9.3, 9.4)})})
    s = ec.summary(db)
    assert s["shorts"] == 3 and s["late"] == 1                             # only the one received after its minute opened
    assert s["official"] == pytest.approx(-0.05)
    assert s["long_stops"] == 1 and s["stop_net"] == pytest.approx(-0.04)
    assert s["low_net"] == pytest.approx(9.3 / 10.0 - 1 - COST)
    assert s["below"] == pytest.approx(9.3 / 9.7 - 1)
    text = ec.line(db)
    assert text.startswith("execution check (a measurement): 3 shorts official -5.0% a trade")
    assert "after the official entry minute on 1" in text and "reread at 50" in text
    assert "1 long stop exits booked at the stop -4.0% a trade, -8.0% at that minute's low" in text


def test_a_low_above_the_booked_exit_never_improves_the_figure(db):
    # a gap: the exit was booked at the minute's open, under the stop; the "low" figure is never above the booked exit
    fid = short(db)
    with db.tx() as c:
        c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, entry, exit, exit_ts, exit_reason, "
                  "net, cost_rt, stop_low, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (fid, "AAA", DAY, "L8", "settled", 9.7, 10.0, 9.5, "2026-11-18 15:10:00+00:00", "stop", -0.06, COST, 9.6, "x"))
    s = ec.summary(db)
    assert s["low_net"] == pytest.approx(9.5 / 10.0 - 1 - COST) and s["below"] == pytest.approx(0.0)


def test_a_second_writer_never_overwrites_a_recorded_figure(db):
    rid = short(db)
    r = {"id": rid, "symbol": "AAA", "session": DAY, "est_entry_at": "2026-11-18T15:01:12+00:00"}
    cost = lambda sym, ts, px: COST  # noqa: E731
    assert ec._short(db, r, lambda sym, session: rth(), cost) is not None
    first = one(db, "fade_trades", rid)["dec_net"]
    assert ec._short(db, r, lambda sym, session: rth({32: (20.0, 20.0, 20.0, 20.0)}), cost) is None
    assert one(db, "fade_trades", rid)["dec_net"] == first


def test_an_alert_recorded_under_the_entry_rule_gets_what_the_official_bars_showed(db):
    """2026-11-25: the live book records, and does not short, an alert whose registered entry minute passed without a
    free-feed print. It cannot know in time whether the stock traded; the official bars say so the same night."""
    from sgt.fast import fade

    def recorded(sym):
        rid = short(db, sym=sym, status="skipped")              # posted 10:00:20 -> registered minute 10:01
        with db.tx() as c:
            c.execute("UPDATE fade_trades SET reason=?, est_entry=NULL, est_entry_at=NULL, entry_ts=NULL, entry=NULL, "
                      "net=NULL WHERE id=?", (fade.SUBSTITUTE_REASON, rid))
        return rid

    traded, delayed, never, nobars = recorded("TRD"), recorded("DLY"), recorded("NVR"), recorded("NOB")
    full = rth()
    cut = lambda a, b: full[(full.ts < OPEN + pd.Timedelta(minutes=a)) | (full.ts >= OPEN + pd.Timedelta(minutes=b))]  # noqa: E731
    days = {"TRD": full, "DLY": cut(31, 33), "NVR": cut(31, 40)}       # 10:01 is minute 31 of the session
    lines = run(db, days)
    assert one(db, "fade_trades", traded)["entry_official"] == ec.OFF_SUBSTITUTE
    assert one(db, "fade_trades", delayed)["entry_official"] == ec.OFF_DELAYED.format(at="10:03")
    assert one(db, "fade_trades", never)["entry_official"] == ec.OFF_NONE
    assert one(db, "fade_trades", nobars)["entry_official"] is None                # no bars yet: retried, not guessed
    assert sorted(x for x in lines if "recorded, not shorted" in x) == [
        "DLY recorded, not shorted: the official tape first traded in a later minute",
        "NVR recorded, not shorted: no official bar within 5 minutes",
        "TRD recorded, not shorted: the official tape traded in its registered minute"]
    assert not [x for x in run(db, days) if "recorded, not shorted" in x]          # written once
    assert all(one(db, "fade_trades", i)["status"] == "skipped" for i in (traded, delayed, never))   # nothing else moves


def test_a_base_break_taken_on_the_free_feed_gets_what_the_official_candles_show(db):
    """BNAI 2026-11-25: on the free feed the base was 7.89 to 8.18 (3.7% wide: a base); on the official tape the same
    ten minutes ran 7.85 to 8.1899 (4.3%: not a base), so the long existed only on the thin feed. A known limit of
    the data source (prereg_long_setup_tape_2026-11-22); now each trade says so the same night."""
    quiet = [(8.0, 8.0, 8.0, 8.0)] * 6                               # 10:15 to 10:20: flat, so VWAP sits at 8.0
    tape_base = [(8.0703, 8.1799, 7.9201, 8.17), (8.1622, 8.1899, 8.0401, 8.0599), (8.0622, 8.1094, 7.93, 7.96),
                 (7.9504, 8.0499, 7.85, 7.98), (7.9601, 8.0399, 7.9217, 7.98), (8.01, 8.0284, 7.9343, 7.9811),
                 (7.98, 8.04, 7.91, 7.9315), (7.95, 8.17, 7.92, 8.15), (8.13, 8.1585, 7.94, 7.9501), (7.95, 8.15, 7.9201, 8.1)]
    brk = (8.09, 8.32, 8.03, 8.255)
    t0 = pd.Timestamp("2026-11-18 15:15", tz="UTC")

    def frame(rows):
        return pd.DataFrame([{"ts": t0 + pd.Timedelta(minutes=i), "segment": "rth", "open": o, "high": h, "low": lo,
                              "close": c, "volume": 1000.0} for i, (o, h, lo, c) in enumerate(rows)])

    posted, ready = pd.Timestamp("2026-11-18 15:14:40", tz="UTC"), t0 + pd.Timedelta(minutes=17)
    wide = frame(quiet + tape_base + [brk])
    assert ec.base_on(wide, posted, ready) == ("official tape: no base break at 10:31: the 10 minutes before it span 4.33% "
                                               "(7.85 to 8.1899; limit 4%)")
    narrow = frame(quiet + [(o, min(h, 8.18), max(lo, 7.89), c) for o, h, lo, c in tape_base] + [brk])
    assert ec.base_on(narrow, posted, ready) == "official tape: the same base break at 10:31 (base 7.89 to 8.18, 3.68% wide)"
    low_close = frame(quiet + [(o, min(h, 8.18), max(lo, 7.89), c) for o, h, lo, c in tape_base] + [(8.09, 8.32, 8.03, 8.15)])
    assert "its close 8.15 is not above their high 8.18" in ec.base_on(low_close, posted, ready)
    fid = short(db, sym="BNAI", posted=posted.isoformat(), status="filtered")
    with db.tx() as c:
        lid = c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, entry_ts, est_entry, "
                        "created_at) VALUES (?,?,?,?,?,?,?,?,?)", (fid, "BNAI", DAY, "L8", "est_closed", 7.89,
                                                                  ready.isoformat(), 8.255, "x")).lastrowid
    lines = run(db, {"BNAI": wide})
    assert one(db, "long_trades", lid)["setup_official"].startswith("official tape: no base break at 10:31: the 10 minutes")
    assert [x for x in lines if x.startswith("BNAI base break: official tape: no base break at 10:31")]
    assert not [x for x in run(db, {"BNAI": narrow}) if "base break" in x]            # written once, never rewritten
    assert one(db, "long_trades", lid)["status"] == "est_closed"                      # evidence only: nothing else moves
    # a long booked a minute AFTER its setup was ready (the feed had no candle in between): the break candle comes from
    # the setup the row kept, not from the entry time
    f2 = short(db, sym="LATE", posted=posted.isoformat(), status="filtered")
    with db.tx() as c:
        l2 = c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, entry_ts, est_entry, "
                       "setup_info, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                       (f2, "LATE", DAY, "L8", "est_closed", 7.89, (ready + pd.Timedelta(minutes=2)).isoformat(), 8.3,
                        '{"break_ts": "' + (ready - pd.Timedelta(minutes=1)).isoformat() + '"}', "x")).lastrowid
    run(db, {"LATE": narrow})
    assert one(db, "long_trades", l2)["setup_official"] == "official tape: the same base break at 10:31 (base 7.89 to 8.18, 3.68% wide)"

