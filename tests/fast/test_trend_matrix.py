"""The trader's trend matrix, live (owner 2026-11-18): shown on long texts, his rule tracked beside every long."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sgt.fast import long_book as lb, trader_rule as tr, trend_matrix as tm
from sgt.ops.store import OpsStore

T0 = pd.Timestamp("2026-11-09 14:30", tz="UTC")                  # 09:30 ET
MIN = pd.Timedelta(minutes=1)


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


@pytest.fixture(autouse=True)
def fresh():
    tr._last.clear()


def candles(closes, start=T0):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"ts": [start + i * MIN for i in range(len(c))], "open": o, "high": np.maximum(o, c) + 0.01,
                         "low": np.minimum(o, c) - 0.01, "close": c, "volume": 1000.0})


UP_DOWN = candles(np.r_[np.linspace(10, 12, 120), np.linspace(12, 10.5, 60), np.linspace(10.5, 12.5, 100)])


def at(minute, second=0):
    return (T0 + pd.Timedelta(minutes=minute, seconds=second)).to_pydatetime()


def test_a_steady_rise_is_all_green_on_every_chart_and_the_line_says_stay_in():
    snap = tm.snapshot(UP_DOWN, at(115))
    assert all(snap[1].values()) and all(snap[5].values()) and set(snap) == {1, 2, 3, 5}
    assert tm.line(snap) == "📐 Trader's matrix 1M 6/6 · 2M 6/6 · 3M 6/6 · 5M 6/6 · 1M all green: his rule says stay in"


def test_after_the_turn_the_line_names_what_is_red_and_macd_is_first():
    snap = tm.snapshot(UP_DOWN, at(128))                          # eight minutes into the fall
    assert snap[1]["macd"] is False and snap[1]["ema"] is True
    ln = tm.line(snap)
    assert "1M red: MACD" in ln and ln.startswith("📐 Trader's matrix 1M ")


def test_only_closed_candles_and_only_completed_higher_charts_are_read():
    f = candles(np.linspace(10, 11, 20))
    assert len(tm.closed(f, at(10, 59))) == 10 and len(tm.closed(f, at(11))) == 11
    five = tm.resample(tm.closed(f, at(14)), 5)                   # minutes 0..13 closed: buckets 0-4 and 5-9 complete
    assert len(five) == 2 and five.close.iloc[-1] == f.close.iloc[9] and five.volume.iloc[0] == 5000
    assert tm.snapshot(f.iloc[:3], at(3)) is None                 # too little to read
    assert tm.snapshot(f, at(20))[5] is None and tm.snapshot(f, at(20))[1] is not None
    assert tm.line(None) is None and tm.line({1: None}) is None


def test_his_exit_is_macd_turning_red_after_it_was_green_since_the_entry():
    entry = T0 + 100 * MIN                                        # bought inside the rise: MACD green
    assert tm.going_red(UP_DOWN, entry, at(118)) is None          # still rising
    x = tm.going_red(UP_DOWN, entry, at(140))
    macd = tm.conditions(UP_DOWN.iloc[:140]).macd.to_numpy()
    j = int(np.flatnonzero(~macd[100:])[0]) + 100
    assert x["why"] == "MACD turned red" and x["at"] == UP_DOWN.ts[j + 1] and x["px"] == UP_DOWN.open[j + 1]
    assert tm.going_red(UP_DOWN, entry, at(j + 1)) == {"px": UP_DOWN.close[j], "at": UP_DOWN.ts[j] + MIN, "why": "MACD turned red"}


def test_macd_red_at_the_entry_is_not_an_exit_until_it_has_been_green():
    entry = T0 + 150 * MIN                                        # bought in the fall: MACD red at the entry
    assert tm.going_red(UP_DOWN, entry, at(175)) is None          # red all along: his rule has not started
    x = tm.going_red(UP_DOWN, entry, at(280))                     # green on the second rise, never red again
    assert x is None


def long_row(db, sym="ABCD", status="est_closed", entry_min=100, est_entry=11.67, est_exit=11.9, fid=1, session="2026-11-09"):
    with db.tx() as c:
        return c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, target, entry_ts, est_entry, "
                         "est_exit, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (fid, sym, session, "L6", status, 11.0, 13.0, (T0 + entry_min * MIN).isoformat(), est_entry, est_exit,
                          "x")).lastrowid


def rows(db):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT symbol, status, tr_exit_px, tr_exit_at, tr_why FROM long_trades ORDER BY id")]


def test_the_rule_is_recorded_once_and_texted_also_after_the_books_own_exit(db):
    long_row(db)
    sent = []
    feed = lambda sym, now: UP_DOWN                               # noqa: E731
    assert tr.track(db, feed, sent.append, at(118)) == 0 and rows(db)[0]["tr_exit_px"] is None
    assert tr.track(db, feed, sent.append, at(140)) == 1
    r = rows(db)[0]
    assert r["tr_why"] == "MACD turned red" and r["tr_exit_px"] == pytest.approx(tm.going_red(UP_DOWN, T0 + 100 * MIN, at(140))["px"])
    assert len(sent) == 1 and sent[0].startswith("[SGT] 📐 TRADER'S RULE ABCD (paper): MACD turned red at ")
    assert "from the $11.67 entry, before costs. Book's own exit: $11.90 (+2.0%)." in sent[0]
    assert tr.track(db, feed, sent.append, at(145)) == 0 and len(sent) == 1
    assert tr.line(db, "2026-11-09").startswith("trader's rule on today's longs (before costs): ABCD book +2.0% / his rule ")


def test_one_look_a_minute_and_no_text_without_a_sender(db):
    long_row(db)
    calls = []

    def feed(sym, now):
        calls.append(now)
        return UP_DOWN
    tr.track(db, feed, None, at(110))
    tr.track(db, feed, None, at(110, 15))
    tr.track(db, feed, None, at(110, 30))
    assert len(calls) == 1
    assert tr.track(db, feed, None, at(140)) == 1 and rows(db)[0]["tr_exit_px"] is not None


def test_still_in_at_the_close_is_recorded_with_the_last_price(db):
    long_row(db, entry_min=200, est_entry=11.0, est_exit=None, status="seen")
    day = candles(np.linspace(10, 14, 390))                       # rises all day: MACD never goes red
    sent = []
    assert tr.track(db, lambda s, n: day, sent.append, at(380)) == 0
    after = (pd.Timestamp("2026-11-09 21:01:30", tz="UTC")).to_pydatetime()
    assert tr.track(db, lambda s, n: day, sent.append, after) == 1
    r = rows(db)[0]
    assert r["tr_why"] == tr.STILL_IN and r["tr_exit_px"] == pytest.approx(14.0)
    assert "(watched, not traded): still in at the close at 16:00 ET" in sent[0] and "Book's own exit" not in sent[0]


def test_other_days_missed_rows_and_failures_are_left_alone(db):
    long_row(db, sym="OLD", session="2026-11-06", fid=1)
    long_row(db, sym="MISS", status="missed", fid=2)
    long_row(db, sym="BAD", fid=3)
    long_row(db, sym="GOOD", fid=4)

    def feed(sym, now):
        if sym == "BAD":
            raise RuntimeError("boom")
        return UP_DOWN
    assert tr.track(db, feed, None, at(140)) == 1
    got = {r["symbol"]: r["tr_exit_px"] is not None for r in rows(db)}
    assert got == {"OLD": False, "MISS": False, "BAD": False, "GOOD": True}


def test_long_texts_carry_the_matrix_line_and_survive_a_broken_matrix(monkeypatch):
    assert lb.matrix_line(UP_DOWN, at(115)).startswith("\n📐 Trader's matrix 1M 6/6")
    assert lb.matrix_line(UP_DOWN.iloc[:2], at(2)) == ""

    def boom(*a):
        raise RuntimeError("x")
    monkeypatch.setattr(tm, "snapshot", boom)
    assert lb.matrix_line(UP_DOWN, at(115)) == ""


def test_the_book_never_reads_the_trader_columns():
    from pathlib import Path

    src = Path(lb.__file__).read_text()
    assert "tr_exit" not in src and "tr_why" not in src            # recorded beside the book, never read by it


def test_his_exit_price_is_the_next_candles_open_when_it_gaps():
    f = UP_DOWN.copy()
    macd = tm.conditions(f.iloc[:140]).macd.to_numpy()
    j = int(np.flatnonzero(~macd[100:])[0]) + 100
    f.loc[j + 1, "open"] = f.close[j] - 0.50                     # the candle after the red close opens lower
    x = tm.going_red(f, T0 + 100 * MIN, at(140))
    assert x["px"] == pytest.approx(f.close[j] - 0.50) and x["px"] != f.close[j]


def test_the_settings_are_the_traders():
    assert (tm.MACD_FAST, tm.MACD_SLOW, tm.MACD_SIG) == (12, 31, 10) and tm.EMA_N == 34 and tm.RSI_N == 14
    assert tm.BB_N == 20 and (tm.ST_N, tm.ST_MULT) == (10, 3.0) and tm.PVT_N == 15 and tm.FRAMES == (1, 2, 3, 5)
    assert tm.NAMES == ("macd", "ema", "rsi", "bb", "supertrend", "pvt")          # his order of importance
