"""The parallel calculation (owner 2026-11-25, option A): price-decimal-v1 beside the registered binary-v0.
Separate fields, explicit labels, the registered figure untouched, nothing restated, nothing mixed."""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
import alert_fade_test as aft  # noqa: E402
import patterns_simulate_v0 as frozen  # noqa: E402
from sgt.fast import calc_v1, exact_price as xp, fade, long_book  # noqa: E402
from sgt.fast import patterns as pt  # noqa: E402
from sgt.ops import digest, revisions  # noqa: E402
from sgt.ops.store import OpsStore  # noqa: E402

T0 = pd.Timestamp("2026-11-17 16:45:00", tz="UTC")              # 11:45 ET
ET_DAY = "2026-11-17"


def bars(rows, start=T0):
    """(open, high, low, close) per minute from ``start``."""
    return pd.DataFrame([{"ts": start + pd.Timedelta(minutes=i), "open": o, "high": h, "low": lo, "close": c, "volume": 1000.0,
                          "segment": "rth"} for i, (o, h, lo, c) in enumerate(rows)])


# ---- the registered simulator is exactly what it was ----
def test_the_registered_simulator_with_its_defaults_returns_exactly_what_the_frozen_copy_returns():
    """20,000 random setups and paths on the cent grid, where exact touches are common: trigger and market entries,
    long and short, with and without the red exit. Not one field may differ."""
    rng, n, touches = random.Random(20261125), 0, 0
    for _ in range(20_000):
        base = rng.choice([1.0, 1.1, 2.5, 5.15, 9.05, 0.8, 12.35])
        px, rows = base, []
        for _ in range(rng.randint(2, 40)):
            o = round(px + rng.choice([-0.03, -0.01, 0, 0.01, 0.02]), 2)
            h = round(max(o, px) + rng.choice([0, 0.01, 0.02, 0.05, 0.07]), 2)
            lo = round(max(0.01, min(o, px) - rng.choice([0, 0.01, 0.02, 0.035])), 4)
            c = round(rng.uniform(lo, h), 2)
            rows.append((o, h, lo, c))
            px = c
        f = bars(rows)
        side = rng.choice(["long", "long", "short"])
        stop = round(base - rng.choice([0.02, 0.035, 0.05]), 3) if side == "long" else round(base + rng.choice([0.02, 0.05]), 3)
        trig = rng.choice([None, round(base + (0.01 if side == "long" else -0.01), 2)])
        s = pt.Setup("L4b", side, T0, trig, stop)
        red = rng.random() < 0.3
        a, b = pt.simulate(f, s, red_exit=red), frozen.simulate(f, s, red_exit=red)
        assert a == b
        n += a is not None
        if a is not None and side == "long":
            touches += any(xp.price(h) == xp.long_target(a.entry, stop) for _, h, _, _ in rows)
    assert n > 5_000 and touches > 300                              # the corpus does exercise fills and exact touches


def test_exact_mode_differs_from_the_registered_one_only_where_a_threshold_is_touched_exactly():
    rain = bars([(1.0701, 1.17, 1.07, 1.13), (1.1318, 1.16, 1.13, 1.1501), (1.135, 1.17, 1.08, 1.11), (1.10, 1.11, 1.05, 1.08)])
    s = pt.Setup("L4b", "long", T0, 1.1, 1.065)
    v0, v1 = pt.simulate(rain, s, red_exit=True), pt.simulate(rain, s, red_exit=True, exact=True)
    assert (v0.entry, v0.reason, v0.exit) == (1.1, "stop", 1.065)                  # RAIN 2026-11-17 as registered
    assert (v1.entry, v1.reason, v1.exit, v1.exit_ts) == (1.1, "target", 1.17, T0)   # the fill minute's 1.17 IS the target
    over = bars([(1.0701, 1.18, 1.07, 1.13), (1.1318, 1.16, 1.05, 1.06)])
    a, b = pt.simulate(over, s), pt.simulate(over, s, exact=True)                  # a print above it: one decision
    assert (a.reason, a.exit_ts, a.entry) == (b.reason, b.exit_ts, b.entry) == ("target", T0, 1.1)
    assert (a.exit, b.exit) == (1.1700000000000004, 1.17)          # each books ITS target: the double, and the exact one
    under = bars([(1.0701, 1.1699, 1.07, 1.13), (1.1318, 1.16, 1.05, 1.06)])
    assert pt.simulate(under, s) == pt.simulate(under, s, exact=True)              # one sub-penny under: identical
    with pytest.raises(xp.NotAPrice):                                              # not a reported price: said, not rounded
        pt.simulate(bars([(1.0701, 1.1700000001, 1.07, 1.13)]), s, exact=True)


def test_the_short_replay_under_v1_equals_the_registered_one_except_at_an_exact_touch():
    rng = random.Random(7)
    for _ in range(3_000):
        entry = rng.choice([1.0, 5.15, 9.05, 2.37, 1.9692, 16.85])
        lv = float(xp.level_above(entry))
        w = bars([(entry, round(entry * rng.uniform(1.0, 1.25), 2), entry * 0.9, entry) for _ in range(rng.randint(1, 30))])
        v = calc_v1.short(w, entry, 0.01, 0.005)
        cover0 = aft.cover(w, entry)
        touched = any(xp.price(h) == xp.level_above(entry) for h in w.high)
        if v["calc_v1"] == calc_v1.SAME:
            assert v["cover_v1"] == pytest.approx(cover0, abs=1e-12)
            assert v["net_v1"] == pytest.approx((entry - cover0) / entry - 0.015, abs=1e-12)
        else:
            assert touched and v["calc_v1"].startswith(calc_v1.DIFFERS) and v["cover_v1"] == pytest.approx(lv)
    hit = calc_v1.short(bars([(5.15, 6.0, 5.1, 5.9), (6.0, 6.18, 5.9, 6.1), (6.1, 6.1, 5.0, 5.0)]), 5.15, 0.01, 0.005)
    assert hit["cover_v1"] == 6.18 and hit["net_v1"] == pytest.approx((5.15 - 6.18) / 5.15 - 0.015)
    assert hit["calc_v1"] == ("price-decimal-v1: DIFFERS from binary-v0: price-decimal-v1 stop reached 11:46 ET; "
                              "binary-v0 no stop")


def test_a_value_that_is_not_a_reported_price_is_a_visible_outcome_never_a_crash_or_a_rounded_price():
    bad = bars([(1.0, 1.1234567, 0.9, 1.0), (1.0, 1.3, 0.9, 1.0)])
    v = calc_v1.short(bad, 1.0, 0.01, 0.005)
    assert set(v) == {"calc_v1"} and v["calc_v1"].startswith("price-decimal-v1: not computed (data quality: ")
    assert "more than 6 decimal places" in v["calc_v1"]
    assert calc_v1.short(None, 1.0, 0.01, 0.005) == calc_v1.not_computed("no official bars for the window")
    assert calc_v1.short(bars([(1, 1, 1, 1)]), float("nan"), 0.01, 0.005)["calc_v1"].startswith(calc_v1.NOT_COMPUTED)
    s = pt.Setup("L4b", "long", T0, 1.1, 1.065)
    lg = calc_v1.long(bars([(1.0701, 1.1700001234, 1.07, 1.13)]), s, pt.simulate(bars([(1.0701, 1.17, 1.07, 1.13)]), s), 0.007)
    assert set(lg) == {"calc_v1"} and "data quality" in lg["calc_v1"]


# ---- the settles: separate fields, the registered figure untouched ----
@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def _short_row(db, sym="AAA", entry=5.15):
    with db.tx() as c:
        return c.execute(
            "INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, weight,"
            " est_entry, est_entry_at, est_last, est_exit, est_exit_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, ET_DAY, "new_high", "2026-11-17T16:44:40+00:00", "-1", hash(sym) % 10_000, "est_closed", "60 min", 0.0125,
             entry, "2026-11-17T16:45:05+00:00", entry, entry, "2026-11-17T17:45:05+00:00", "2026-11-17T16:44:42+00:00")).lastrowid


def test_the_short_settle_books_the_registered_figure_and_stores_the_v1_figure_beside_it(db):
    rid = _short_row(db)
    w = bars([(5.15, 6.0, 5.1, 5.9), (6.0, 6.18, 5.9, 6.1), (6.1, 6.1, 5.0, 5.0)])
    cover0 = aft.cover(w, 5.15)                                       # binary-v0: 6.18 is under 6.180000000000001: no stop
    net0 = (5.15 - cover0) / 5.15 - 0.015
    res = pd.DataFrame([{"id": rid, "qualifies": True, "why": "", "entry_ts": "2026-11-17 16:45:00+00:00", "entry": 5.15,
                         "cover": cover0, "cost_rt": 0.01, "gross": (5.15 - cover0) / 5.15, "net": net0,
                         "rule_entry": 5.15, "rule_net": net0, **calc_v1.short(w, 5.15, 0.01, 0.005)}])
    before = fade.nav(db)
    assert fade.settle(db, lambda due: res, now=datetime(2026, 11, 18, 1, 40, tzinfo=timezone.utc)) == [f"AAA {net0:+.2%}"]
    with db.read() as c:
        r = dict(c.execute("SELECT * FROM fade_trades WHERE id=?", (rid,)).fetchone())
    assert (r["cover"], r["net"], r["calc"]) == (5.0, pytest.approx(net0), "binary-v0")         # the registered result
    assert r["cover_v1"] == 6.18 and r["net_v1"] == pytest.approx(-0.2 - 0.015) and r["calc_v1"].startswith(calc_v1.DIFFERS)
    # nothing that counts reads the v1 columns: the book's value, the day's dollars and the revision block
    assert fade.nav(db) == pytest.approx(before * (1 + 0.0125 * net0))
    assert fade.settled_exit(r) == "60 min"
    (item,) = revisions.collect(db, datetime(2026, 11, 17).date())[0]
    assert item.settled.net == pytest.approx(net0) and item.settled.exit == 5.0
    line = calc_v1.line(db)
    assert line == ("arithmetic check since 11-25 (price-decimal-v1 beside the registered binary-v0; the registered "
                    f"figure decides everything): 1 settled trade computed both ways, 1 decided differently: AAA short "
                    f"11-17 registered {net0:+.1%} / price-decimal-v1 -21.5%")
    assert line in digest.build(db, datetime(2026, 11, 18, 20, 50, tzinfo=timezone.utc), fills=[], problems=[],
                                broker=None, research=[])


def test_a_scorer_that_supplies_no_v1_values_settles_exactly_as_before(db):
    rid = _short_row(db, "BBB", 2.0)
    res = pd.DataFrame([{"id": rid, "qualifies": True, "why": "", "entry_ts": "2026-11-17 16:45:00+00:00", "entry": 2.0,
                         "cover": 1.9, "cost_rt": 0.01, "gross": 0.05, "net": 0.035}])
    assert fade.settle(db, lambda due: res, now=datetime(2026, 11, 18, 1, 40, tzinfo=timezone.utc)) == ["BBB +3.50%"]
    with db.read() as c:
        r = dict(c.execute("SELECT * FROM fade_trades WHERE id=?", (rid,)).fetchone())
    assert (r["net"], r["calc"], r["net_v1"], r["cover_v1"], r["calc_v1"]) == (0.035, "binary-v0", None, None, None)
    assert calc_v1.line(db) is None                                  # no trade carries both figures: no line


def test_the_long_settle_books_the_registered_result_and_stores_the_v1_result_beside_it(db):
    fid = _short_row(db, "RAIN", 1.1)
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET status='filtered' WHERE id=?", (fid,))
        lid = c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, trigger, stop, target, weight, "
                        "entry_ts, est_entry, est_exit, est_exit_at, reason, rules, created_at) VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (fid, "RAIN", ET_DAY, "L4b", "est_closed", 1.1, 1.065, 1.185, 0.037, T0.isoformat(), 1.105, 1.06,
                         (T0 + pd.Timedelta(minutes=11)).isoformat(), "stop", long_book.RULES, T0.isoformat())).lastrowid
    rain = bars([(1.0701, 1.17, 1.07, 1.13), (1.1318, 1.16, 1.13, 1.1501), (1.135, 1.17, 1.08, 1.11), (1.10, 1.11, 1.05, 1.08)])
    before = long_book.nav(db)
    out = long_book.settle(db, lambda sym, ses: rain, lambda sym, ts, px: 0.0073386,
                           now=datetime(2026, 11, 18, 1, 40, tzinfo=timezone.utc))
    assert out == ["RAIN L4b -3.92%"]
    with db.read() as c:
        r = dict(c.execute("SELECT * FROM long_trades WHERE id=?", (lid,)).fetchone())
    assert (r["status"], r["exit"], r["exit_reason"], r["calc"]) == ("settled", 1.065, "stop", "binary-v0")
    assert r["net"] == pytest.approx(1.065 / 1.1 - 1 - 0.0073386)
    assert (r["exit_v1"], r["exit_reason_v1"]) == (1.17, "target") and r["net_v1"] == pytest.approx(1.17 / 1.1 - 1 - 0.0073386)
    assert r["calc_v1"] == "price-decimal-v1: DIFFERS from binary-v0: price-decimal-v1 target 11:45 ET; binary-v0 stop 11:48 ET"
    assert long_book.nav(db) == pytest.approx(before * (1 + 0.037 * r["net"]))     # the book moves by the registered net
    assert long_book.settle(db, lambda sym, ses: rain, lambda sym, ts, px: 0.0073386,
                            now=datetime(2026, 11, 18, 2, 40, tzinfo=timezone.utc)) == []      # written once
    assert "RAIN long 11-17 registered -3.9% / price-decimal-v1 +5.6%" in calc_v1.line(db)


def test_nothing_settled_before_the_parallel_figure_existed_gets_one(db):
    rid = _short_row(db, "OLD", 2.0)
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET status='settled', entry=2.0, cover=1.9, net=0.035, settled_at=? WHERE id=?",
                  ("2026-11-18T01:40:00+00:00", rid))
    assert fade.settle(db, lambda due: pd.DataFrame(), now=datetime(2026, 11, 19, 1, 40, tzinfo=timezone.utc)) == []
    with db.read() as c:
        r = c.execute("SELECT calc, calc_v1, net_v1 FROM fade_trades WHERE id=?", (rid,)).fetchone()
    assert tuple(r) == (None, None, None) and calc_v1.line(db) is None
    assert timedelta(0) == timedelta(0)


def test_the_scorer_attaches_v1_values_without_moving_one_registered_number():
    import fade_live as fl

    w = bars([(5.15, 6.0, 5.1, 5.9), (6.0, 6.18, 5.9, 6.1), (6.1, 6.1, 5.0, 5.0)])

    class Store:
        def __init__(self, fail=False):
            self.fail = fail

        def read_frame(self, symbol, a, b):
            if self.fail:
                raise OSError("store unreadable")
            return w.assign(ts=w.ts) if symbol == "AAA" else w.iloc[0:0]

    due = pd.DataFrame([{"id": 1, "symbol": "AAA", "session": ET_DAY, "reason": "60 min"},
                        {"id": 2, "symbol": "AAA", "session": ET_DAY, "reason": fade.VETO_EXIT},
                        {"id": 3, "symbol": "AAA", "session": ET_DAY, "reason": "60 min"},
                        {"id": 4, "symbol": "ZZZ", "session": ET_DAY, "reason": "60 min"},
                        {"id": 5, "symbol": "AAA", "session": ET_DAY, "reason": "60 min"}])
    base = {"entry_ts": T0, "entry": 5.15, "cost_rt": 0.01, "gross": 0.029, "net": 0.014, "cover": 5.0}
    out = pd.DataFrame([{"id": 1, "qualifies": True, "why": "", **base}, {"id": 2, "qualifies": True, "why": "", **base},
                        {"id": 3, "qualifies": True, "why": fl.LIVE_ENTRY.format(why="x"), **base},
                        {"id": 4, "qualifies": True, "why": "", **base},
                        {"id": 5, "qualifies": False, "why": "void", **{**base, "net": None}}])
    got = fl.with_v1(out, due, Store()).set_index("id")
    pd.testing.assert_frame_equal(got[list(out.columns.drop("id"))], out.set_index("id"))      # registered values: identical
    assert got.loc[1, "cover_v1"] == 6.18 and got.loc[1, "calc_v1"].startswith(calc_v1.DIFFERS)
    assert got.loc[2, "calc_v1"] == "price-decimal-v1: not computed (the owner's NO exit)" and pd.isna(got.loc[2, "net_v1"])
    assert "scored at the live entry" in got.loc[3, "calc_v1"] and "no official bars" in got.loc[4, "calc_v1"]
    assert got.loc[5, "calc_v1"] is None                              # no registered result: no parallel figure either
    broken = fl.with_v1(out, due, Store(fail=True)).set_index("id")   # the store fails: said per trade, nothing raised
    pd.testing.assert_frame_equal(broken[list(out.columns.drop("id"))], out.set_index("id"))
    assert "not computed (error: OSError)" in broken.loc[1, "calc_v1"]


def test_review_findings_of_2026_11_25(db):
    """(1) The one-time re-score of a long settled long ago gives it no v1 figure: nothing settled before the parallel
    figure existed is restated. (2) A short's target is exact too. (3) Bars that are not 64-bit floats are named."""
    fid = _short_row(db, "OLDL", 1.1)
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET status='filtered' WHERE id=?", (fid,))
        lid = c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, trigger, stop, target, weight, "
                        "entry_ts, est_entry, est_exit, reason, net, rules, settled_at, created_at) VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (fid, "OLDL", ET_DAY, "L4b", "settled", 1.1, 1.065, 1.185, 0.037, T0.isoformat(), 1.105, 1.06, "stop",
                         -0.05, long_book.RULES, "2026-11-10T01:40:00+00:00", T0.isoformat())).lastrowid
    rain = bars([(1.0701, 1.17, 1.07, 1.13), (1.1318, 1.16, 1.13, 1.1501), (1.135, 1.17, 1.08, 1.11), (1.10, 1.11, 1.05, 1.08)])
    out = long_book.settle(db, lambda sym, ses: rain, lambda sym, ts, px: 0.0073386,
                           now=datetime(2026, 11, 26, 1, 40, tzinfo=timezone.utc))
    assert len(out) == 1 and "re-scored" in out[0]
    with db.read() as c:
        r = dict(c.execute("SELECT * FROM long_trades WHERE id=?", (lid,)).fetchone())
    assert r["entry"] == 1.1 and (r["net_v1"], r["exit_v1"], r["calc_v1"]) == (None, None, None)
    assert calc_v1.line(db) is None
    # (2)
    from decimal import Decimal as D
    assert xp.short_target(1.000001, 1.5) == D("0.000003") and xp.short_target(10, 10.5) == D("9")
    s = pt.Setup("S2v2", "short", T0, None, 10.5)
    t = pt.simulate(bars([(10.0, 10.1, 9.9, 10.0), (10.0, 10.0, 9.0, 9.2)]), s, exact=True)
    assert (t.reason, t.exit) == ("target", 9.0)
    # (3)
    narrow = bars([(5.15, 6.0, 5.1, 5.9)]).astype({"high": "float32"})
    assert calc_v1.short(narrow, 5.15, 0.01, 0.005) == calc_v1.not_computed("data quality: bar prices are not 64-bit floats (high)")
    assert "not 64-bit floats" in calc_v1.long(narrow, pt.Setup("L4b", "long", T0, 1.1, 1.065), None, 0.007)["calc_v1"]

