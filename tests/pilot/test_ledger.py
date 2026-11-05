from __future__ import annotations

import multiprocessing as mp
from pathlib import Path

import pytest

from sgt.ops.store import OpsStore
from sgt.pilot import ledger
from sgt.pilot.ledger import CapRefused

MARKS = {"SPY": 500.0, "IEF": 100.0, "ABCD": 10.0, "WXYZ": 4.0}


@pytest.fixture
def db(tmp_path: Path) -> OpsStore:
    s = OpsStore(tmp_path / "ops.db")
    ledger.init(s)
    return s


def _res(db, cid, strategy="6040", symbol="SPY", side="BUY", qty=3, px=500.0, marks=MARKS):
    ledger.reserve(db, client_order_id=cid, strategy=strategy, symbol=symbol, side=side, qty=qty, limit_price=px,
                   marks=marks)


def test_per_order_and_subcap(db):
    with pytest.raises(CapRefused, match="per-order"):
        _res(db, "a", qty=5)                                   # $2,500
    _res(db, "b", qty=4)                                       # $2,000
    _res(db, "c", qty=4)                                       # $4,000 = 60/40 sub-cap
    with pytest.raises(CapRefused, match="6040 budget"):
        _res(db, "d", symbol="IEF", qty=1, px=100.0)


def test_hard_cap_counts_positions_at_mark_and_working_orders(db):
    _res(db, "f1", strategy="fade", symbol="ABCD", side="SELL_SHORT", qty=200, px=10.0)
    ledger.fill(db, "f1", 200, 10.0)
    _res(db, "f2", strategy="fade", symbol="WXYZ", side="SELL_SHORT", qty=500, px=4.0)   # working, unfilled
    for i in range(4):
        _res(db, f"s{i}", qty=2)                               # 60/40 $4,000
    _res(db, "t1", strategy="tom", qty=2)                      # $1,000 -> total 2,000+2,000+4,000+1,000
    _res(db, "t2", strategy="tom", qty=1)                      # +500 = 9,500, fits at entry marks
    # sub-caps sum to the hard cap, so it binds when marks move: ABCD short rallied 10 -> 15 (+1,000)
    with pytest.raises(CapRefused, match="total"):
        _res(db, "t3", strategy="tom", qty=1, marks={**MARKS, "ABCD": 15.0})
    # the short rallied: exposure at the live mark, not the entry price
    with pytest.raises(CapRefused, match="fade budget"):
        _res(db, "f3", strategy="fade", symbol="WXYZ", side="SELL_SHORT", qty=1, px=4.0,
             marks={**MARKS, "ABCD": 10.0 * 1.9})


def test_released_and_partial_fills(db):
    for i in range(2):
        _res(db, f"o{i}", qty=4)
    ledger.fill(db, "o0", 1, 500.0)
    ledger.close(db, "o0", "canceled", "timeout")               # 3 unfilled shares released, 1 held
    _res(db, "o2", qty=3)                                      # 500 + 2,000 + 1,500 = 4,000
    assert ledger.exposure(db, MARKS, "6040") == pytest.approx(4_000)


def test_fill_is_idempotent_by_cumulative_qty(db):
    _res(db, "o", qty=4)
    assert ledger.fill(db, "o", 2, 500.0) == 2
    assert ledger.fill(db, "o", 2, 500.0) == 0                 # replayed report
    assert ledger.fill(db, "o", 4, 501.0) == 2
    (p,) = ledger.positions(db)
    assert (p.qty, p.cost) == (4, pytest.approx(501.0))       # second lot at 502
    with pytest.raises(ValueError):
        ledger.fill(db, "o", 5, 501.0)


def test_closing_orders_capped_at_pilot_quantity(db):
    _res(db, "s", strategy="fade", symbol="ABCD", side="SELL_SHORT", qty=100, px=10.0)
    ledger.fill(db, "s", 100, 10.0)
    with pytest.raises(CapRefused, match="own 100"):
        _res(db, "c1", strategy="fade", symbol="ABCD", side="BUY_TO_COVER", qty=101, px=10.0)
    _res(db, "c1", strategy="fade", symbol="ABCD", side="BUY_TO_COVER", qty=60, px=10.0)
    with pytest.raises(CapRefused, match="60 working"):
        _res(db, "c2", strategy="fade", symbol="ABCD", side="BUY_TO_COVER", qty=41, px=10.0)
    with pytest.raises(CapRefused, match="own 0"):              # the pilot never sells shares it does not hold
        _res(db, "x", symbol="SPY", side="SELL", qty=1)
    ledger.fill(db, "c1", 60, 9.0)
    (p,) = ledger.positions(db)
    assert (p.qty, p.cost) == (-40, 10.0)


def test_exits_allowed_while_killed_entries_refused(db):
    _res(db, "b", qty=2)
    ledger.fill(db, "b", 2, 500.0)
    ledger.kill(db)
    with pytest.raises(CapRefused, match="paused"):
        _res(db, "b2", qty=1)
    _res(db, "x", side="SELL", qty=2)                           # exit still goes
    db.set_flag("paused", "1")
    ledger.kill(db, False)
    with pytest.raises(CapRefused, match="paused"):
        _res(db, "b3", qty=1)


def test_missing_mark_opened_symbol_refuses_other_holdings_count_conservatively(db):
    """Review 2026-11-05 F-A: a holding without a live mark counts at cost x 1.2 instead of blocking every entry;
    the symbol being opened always needs a valid live price."""
    _res(db, "b", qty=2)
    ledger.fill(db, "b", 2, 500.0)
    for marks in ({"SPY": 500.0}, {**MARKS, "IEF": 0.0}, {**MARKS, "IEF": float("nan")}):
        with pytest.raises(CapRefused, match="no valid price for IEF"):
            _res(db, "n", symbol="IEF", qty=1, px=100.0, marks=marks)
    assert ledger.exposure(db, {}, "6040") == pytest.approx(2 * 500 * 1.2)
    _res(db, "n", symbol="IEF", qty=1, px=100.0, marks={"IEF": 100.0})    # SPY unpriced: still trades
    _res(db, "k", symbol="IEF", qty=20, px=100.0, marks={"IEF": 100.0})     # 1,200 + 100 + 2,000 = 3,300
    with pytest.raises(CapRefused, match="6040 budget"):                    # + 800 > 4,000
        _res(db, "m", symbol="IEF", qty=8, px=100.0, marks={"IEF": 100.0})


def test_cross_strategy_entries_with_own_symbol_marks_only(db):
    """Review 2026-11-05 F-A repro, both directions: callers pass only their own symbol's mark."""
    _res(db, "s", qty=3, px=500.0, marks={"SPY": 500.0})
    ledger.fill(db, "s", 3, 500.0)
    _res(db, "f", strategy="fade", symbol="ABCD", side="SELL_SHORT", qty=50, px=10.0, marks={"ABCD": 10.0})
    ledger.fill(db, "f", 50, 10.0)
    _res(db, "i", symbol="IEF", qty=10, px=100.0, marks={"IEF": 100.0})


def test_rejects_bad_inputs_and_duplicates(db):
    for kw, msg in (({"side": "SELL_SHORT_EXEMPT"}, "side"), ({"strategy": "megacap"}, "budget"),
                    ({"qty": 0}, "bad"), ({"px": float("inf")}, "bad")):
        with pytest.raises(CapRefused, match=msg):
            _res(db, "z", **kw)
    _res(db, "d", qty=1)
    with pytest.raises(CapRefused, match="already exists"):
        _res(db, "d", qty=1)
    _res(db, "s", strategy="fade", symbol="ABCD", side="SELL_SHORT", qty=10, px=10.0)
    ledger.fill(db, "s", 10, 10.0)
    with pytest.raises(CapRefused, match="against"):           # no long on top of the pilot's own short
        _res(db, "l", strategy="fade", symbol="ABCD", side="BUY", qty=10, px=10.0)


def _race(path: str, cid: str, q) -> None:
    try:
        ledger.reserve(OpsStore(Path(path)), client_order_id=cid, strategy="tom", symbol="SPY", side="BUY", qty=3,
                       limit_price=500.0, marks=MARKS)
        q.put("ok")
    except CapRefused:
        q.put("refused")


def test_two_processes_cannot_both_pass_the_cap(db):
    """F-6: listener and cron race for the last $1,500 of a $2,000 sub-cap; exactly one wins."""
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_race, args=(str(db.path), f"r{i}", q)) for i in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
    assert sorted(q.get(timeout=5) for _ in procs) == ["ok"] + ["refused"] * 5


def test_order_id_is_deterministic_and_bounded():
    assert ledger.order_id("fade", "812", "entry") == ledger.order_id("fade", "812", "entry") == "pilot:fade:812:entry"
    assert ledger.order_id("fade", "812", "cover2") != ledger.order_id("fade", "812", "cover1")
    for bad in ("", "a:b", "x" * 25, "a b", "a\n"):
        with pytest.raises(ValueError):
            ledger.order_id("fade", bad, "entry")


def test_calendar_knows_the_pilot_half_days():
    """F-7: first TOM entry 2026-11-27 and Christmas Eve close at 13:00 ET; exits must use these bounds."""
    from datetime import date, datetime, timezone

    from sgt.intraday.calendar import default_calendar
    cal = default_calendar()
    for d in (date(2026, 11, 27), date(2026, 12, 24)):
        b = cal.bounds(d)
        assert b.early_close and b.rth_close == datetime(d.year, d.month, d.day, 18, 0, tzinfo=timezone.utc)
    assert not cal.bounds(date(2026, 11, 30)).early_close
