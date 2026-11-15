"""Daily-book positions as trades (owner 2026-11-14): one trade per position from the close it was entered at to the
close it was exited at, with the book's own dollars."""
from __future__ import annotations

import json

import pytest

from sgt.ops import model_trades as mt
from sgt.ops.book import COST_BP
from sgt.ops.store import OpsStore

C = COST_BP / 1e4


@pytest.fixture
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def order(db, strategy, symbol, target, session, price, status="filled"):
    with db.tx() as c:
        c.execute("INSERT INTO book_orders(strategy, symbol, target, fill_session, source, status, fill_price, "
                  "created_at) VALUES (?,?,?,?,?,?,?,?)",
                  (strategy, symbol, target, session, "auto", status, price, f"{session}T20:50:00+00:00"))


def nav(db, strategy, session, value, ret=0.0):
    with db.tx() as c:
        c.execute("INSERT INTO book_nav(strategy, session, nav, ret, holdings) VALUES (?,?,?,?,?)",
                  (strategy, session, value, ret, "{}"))


def test_a_one_day_long_and_a_short_are_two_closed_trades_with_book_dollars(db):
    for d, v in (("2026-11-12", 100_000), ("2026-11-13", 99_000)):
        nav(db, "megacap_ext", d, v)
    order(db, "megacap_ext", "AMD", 0.13, "2026-11-12", 585.30)
    order(db, "megacap_ext", "AMD", 0.0, "2026-11-13", 534.06)
    order(db, "megacap_ext", "TSLA", -0.21, "2026-11-12", 397.45)
    order(db, "megacap_ext", "TSLA", 0.0, "2026-11-13", 390.22)
    amd, tsla = mt.episodes(db)
    assert (amd["symbol"], amd["side"], amd["status"], amd["entry_session"], amd["exit_session"]) == \
        ("AMD", "long", "closed", "2026-11-12", "2026-11-13")
    assert amd["key"].startswith("book:") and amd["sessions"] == 1
    assert amd["gross"] == pytest.approx(534.06 / 585.30 - 1) and amd["net"] == pytest.approx(amd["gross"] - 2 * C)
    assert amd["usd"] == pytest.approx(0.13 * 100_000 * (534.06 / 585.30 - 1) - 0.13 * 100_000 * C - 0.13 * 99_000 * C)
    assert tsla["side"] == "short" and tsla["gross"] == pytest.approx(-(390.22 / 397.45 - 1)) and tsla["usd"] > 0


def test_a_resize_keeps_the_trade_open_and_a_flip_closes_and_opens(db):
    for d in ("2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12"):
        nav(db, "megacap", d, 100_000)
    order(db, "megacap", "NVDA", 0.20, "2026-11-09", 100.0)
    order(db, "megacap", "NVDA", 0.10, "2026-11-10", 110.0)        # same side, smaller: still one trade
    order(db, "megacap", "NVDA", -0.10, "2026-11-11", 121.0)       # flip: the long ends, a short starts
    got = mt.episodes(db)
    assert [(e["side"], e["status"], e["entry_session"], e["exit_session"]) for e in got] == [
        ("long", "closed", "2026-11-09", "2026-11-11"), ("short", "open", "2026-11-11", None)]
    long = got[0]
    assert long["sessions"] == 2 and long["gross"] == pytest.approx(0.21)
    assert long["usd"] == pytest.approx(0.20 * 100_000 * 0.10 + 0.10 * 100_000 * 0.10
                                        - 0.20 * 100_000 * C      # entry
                                        - 0.10 * 100_000 * C      # the resize 20% -> 10% is turnover too
                                        - 0.10 * 100_000 * C)     # exit
    assert got[1]["net"] is None and got[1]["last_px"] == 121.0 and got[1]["last_w"] == -0.10
    assert got[1]["sessions"] == 1                                   # marked once after its entry (11-12)


def test_a_one_stock_books_trade_dollars_equal_the_books_own_change(db):
    """Audit 2026-11-15: marked exactly as book.settle marks (cost on the value before the day's mark, on every change
    of weight), a book that only ever held this one trade must have made exactly the trade's dollars."""
    v0 = 100_000.0
    r1 = -0.20 * C                                   # 11-09: enter 20% at the close
    r2 = 0.20 * 0.10 - 0.10 * C                      # 11-10: +10% on 20%, cut to 10%
    r3 = 0.10 * 0.10 - 0.10 * C                      # 11-11: +10% on 10%, out
    v1 = v0 * (1 + r1)
    v2 = v1 * (1 + r2)
    v3 = v2 * (1 + r3)
    for d, v, r in (("2026-11-09", v1, r1), ("2026-11-10", v2, r2), ("2026-11-11", v3, r3)):
        nav(db, "megacap_long", d, v, r)
    order(db, "megacap_long", "NVDA", 0.20, "2026-11-09", 100.0)
    order(db, "megacap_long", "NVDA", 0.10, "2026-11-10", 110.0)
    order(db, "megacap_long", "NVDA", 0.0, "2026-11-11", 121.0)
    (e,) = mt.episodes(db)
    assert e["status"] == "closed" and e["usd"] == pytest.approx(v3 - v0, abs=1e-6)
    assert e["entry_nav"] == pytest.approx(v1)       # dollars in the position: the book's value once it is on


def _settled_book(db, drop_legs=False):
    """A two-stock book run through the real ``book.settle``: X held four sessions with one resize, Y entered, held
    two sessions without a new order, flipped short and left open."""
    from datetime import date, datetime, timezone

    import pandas as pd

    from sgt.ops import book

    days = [date(2026, 11, d) for d in (2, 3, 4, 5, 6)]
    frame = pd.DataFrame({"X": [100.0, 108.0, 97.0, 111.0, 104.0], "Y": [50.0, 46.0, 53.0, 49.0, 55.0]}, index=days)

    def closes(symbols, start, end):
        return frame[[s for s in symbols if s in frame]].loc[(frame.index >= start) & (frame.index <= end)]
    for d, targets in ((days[0], {"X": 0.6, "Y": 0.3}), (days[2], {"X": 0.4, "Y": 0.3}),      # targets = the whole book
                       (days[3], {"X": 0.4, "Y": -0.2}), (days[4], {"Y": -0.2})):
        book.propose(db, lambda t: None, strategy="b", targets=targets, fill_session=d,
                     deadline=datetime(2099, 1, 1, tzinfo=timezone.utc), label="b", auto_all=True)
        assert book.settle(db, closes, d)[1] == []
    if drop_legs:
        with db.tx() as c:
            c.execute("DELETE FROM book_legs")
    return book, closes, frame


def test_the_trades_of_a_book_add_up_to_the_books_own_change_over_multi_session_holds(db):
    book, _, frame = _settled_book(db)
    got = mt.episodes(db)
    assert [(e["symbol"], e["side"], e["status"], e["sessions"]) for e in got] == [
        ("X", "long", "closed", 4), ("Y", "long", "closed", 3), ("Y", "short", "open", 1)]
    _, value, last = book.holdings(db, "b")
    assert last == "2026-11-06" and abs(value - book.NAV0) > 500                    # a real move, not a flat book
    assert sum(e["usd"] for e in got) == pytest.approx(value - book.NAV0, abs=1e-6)  # to the cent, open trade included
    short = got[2]                                    # open: booked through the last mark; an estimate runs on from there
    assert (short["last_at"], short["last_px"], short["last_w"]) == ("2026-11-06", 55.0, -0.2)
    assert short["nav"] == pytest.approx(value)
    # constant weight on each day's book value is not shares held: fill to fill misses the book on the same trade
    x = got[0]
    assert abs(x["usd"] - (0.6 * book.NAV0 * (97 / 100 - 1) + 0.4 * book.NAV0 * (104 / 97 - 1))) > 100


def test_a_stretch_the_book_has_no_rows_for_falls_back_to_fill_to_fill(db):
    book, _, _ = _settled_book(db, drop_legs=True)
    got = mt.episodes(db)
    with db.tx() as c:
        v = {r["session"]: r["nav"] for r in c.execute("SELECT session, nav FROM book_nav")}
        pre = {r["session"]: r["nav"] / (1 + r["ret"]) for r in c.execute("SELECT session, nav, ret FROM book_nav")}
    x = got[0]                                        # 0.6 from 11-02, resized to 0.4 on 11-04, out on 11-06
    assert x["usd"] == pytest.approx(
        0.6 * v["2026-11-02"] * (97 / 100 - 1) + 0.4 * v["2026-11-04"] * (104 / 97 - 1)
        - (0.6 * pre["2026-11-02"] + 0.2 * pre["2026-11-04"] + 0.4 * pre["2026-11-06"]) * C)
    short = got[2]                                    # open and unbooked: the page estimates it from its fill
    assert (short["last_at"], short["last_px"]) == ("2026-11-05", 49.0)
    assert short["usd"] == pytest.approx(-0.2 * pre["2026-11-05"] * C)
    estimate = sum(e["usd"] for e in got) - 0.2 * v["2026-11-05"] * (55 / 49 - 1)   # what the page showed before the rows
    assert abs(estimate - (v["2026-11-06"] - book.NAV0)) > 50                       # off the book: the audit's finding


def test_one_missing_session_row_sends_only_that_stretch_back_to_the_estimate(db):
    book, _, _ = _settled_book(db)
    exact = {e["key"]: e["usd"] for e in mt.episodes(db)}
    with db.tx() as c:
        c.execute("DELETE FROM book_legs WHERE symbol='X' AND session='2026-11-03'")
    got = {e["key"]: e for e in mt.episodes(db)}
    x = next(e for e in got.values() if e["symbol"] == "X")
    assert x["usd"] != pytest.approx(exact[x["key"]], abs=1.0)                     # 11-02..11-04 is estimated again
    assert all(e["usd"] == pytest.approx(exact[k]) for k, e in got.items() if e["symbol"] == "Y")


def test_an_open_trade_held_over_several_marks_shows_the_books_dollars_and_runs_on_from_the_last_close(db):
    from datetime import date, datetime, timezone

    import pandas as pd

    from sgt.ops import book, charts

    days = [date(2026, 11, d) for d in (9, 10, 11, 12)]
    frame = pd.DataFrame({"SPY": [600.0, 630.0, 590.0, 612.0]}, index=days)
    book.propose(db, lambda t: None, strategy="etf:B_6040", targets={"SPY": 0.6}, fill_session=days[0],
                 deadline=datetime(2099, 1, 1, tzinfo=timezone.utc), label="b", auto_all=True)
    assert book.settle(db, lambda s, a, b: frame.loc[(frame.index >= a) & (frame.index <= b)], days[3])[1] == []
    (e,) = mt.episodes(db)
    value = book.holdings(db, "etf:B_6040")[1]
    assert e["status"] == "open" and e["sessions"] == 3 and e["usd"] == pytest.approx(value - book.NAV0, abs=1e-6)
    assert (e["last_px"], e["last_at"], e["nav"]) == (612.0, "2026-11-12", pytest.approx(value))
    assert abs(e["usd"] - (0.6 * book.NAV0 * (612 / 600 - 1) - 0.6 * book.NAV0 * C)) > 50     # not shares held
    daily = _bars("2026-11-09T05:00:00Z", 4, "1D", p0=600.0)                 # the page's bars end on the last mark
    v = charts.book_view(e, daily, decided="2026-11-09T20:50:00+00:00", entered="2026-11-09T20:59:00+00:00",
                         exited=None, timeframe="1Day", book="60/40")
    assert v["usd"] == pytest.approx(value - book.NAV0, abs=1e-6) and not v["official"]
    ahead = _bars("2026-11-09T05:00:00Z", 5, "1D", p0=600.0)                 # one more session, not marked yet
    v = charts.book_view(e, ahead, decided="2026-11-09T20:50:00+00:00", entered="2026-11-09T20:59:00+00:00",
                         exited=None, timeframe="1Day", book="60/40")
    last = float(ahead.close.iloc[-1])
    assert v["usd"] == pytest.approx(value - book.NAV0 + 0.6 * value * (last / 612.0 - 1), abs=1e-6)


def test_several_orders_for_one_symbol_in_a_session_are_the_one_change_the_book_made(db):
    from datetime import date

    import pandas as pd

    from sgt.ops import book

    days = [date(2026, 11, d) for d in (6, 9, 10)]
    frame = pd.DataFrame({"NVDA": [186.64, 178.67, 168.89], "MSFT": [530.0, 545.0, 533.0]}, index=days)

    def closes(symbols, start, end):
        return frame[[x for x in symbols if x in frame]].loc[(frame.index >= start) & (frame.index <= end)]

    def sched(symbol, target, session):
        order(db, "megacap", symbol, target, session, None, status="scheduled")
        with db.tx() as c:
            return c.execute("SELECT max(id) FROM book_orders").fetchone()[0]
    sched("NVDA", 0.1937, "2026-11-06")
    sched("MSFT", 0.2, "2026-11-06")
    sched("NVDA", 0.0, "2026-11-09")                 # the 11-09 sequence on the runner: out, short, a smaller short
    first_short = sched("NVDA", -0.2327, "2026-11-09")
    sched("NVDA", -0.2033, "2026-11-09")
    sched("MSFT", 0.0, "2026-11-09")                 # out and straight back in at the same weight: the book never moved
    sched("MSFT", 0.2, "2026-11-09")
    assert book.settle(db, closes, days[2])[1] == []
    got = mt.episodes(db)
    value = book.holdings(db, "megacap")[1]
    assert sum(e["usd"] for e in got) == pytest.approx(value - book.NAV0, abs=1e-6)
    assert [(e["symbol"], e["side"], e["status"], e["weight"]) for e in got] == [
        ("NVDA", "long", "closed", 0.1937), ("MSFT", "long", "open", 0.2), ("NVDA", "short", "open", -0.2033)]
    short = got[2]                                   # named after the order that first took the side: its key stays put
    assert short["key"] == f"book:{first_short}" and short["last_w"] == -0.2033
    msft = got[1]                                    # one trade all along, no cost on 11-09
    assert msft["sessions"] == 2 and msft["exit_session"] is None
    with db.tx() as c:
        pre = {r["session"]: r["nav"] / (1 + r["ret"]) for r in c.execute("SELECT session, nav, ret FROM book_nav")}
        booked = c.execute("SELECT sum(usd) FROM book_legs WHERE symbol='MSFT'").fetchone()[0]
    assert msft["usd"] == pytest.approx(booked - 0.2 * pre["2026-11-06"] * C, abs=1e-6)


def test_orders_waiting_for_the_close_are_netted_but_never_with_filled_ones(db):
    nav(db, "megacap", "2026-11-12", 100_000)
    order(db, "megacap", "AMD", 0.2, "2026-11-12", 100.0)
    order(db, "megacap", "AMD", 0.0, "2026-11-12", None, status="scheduled")    # arrived after the mark: not the book yet
    order(db, "megacap", "JPM", 0.1, "2026-11-13", None, status="scheduled")
    order(db, "megacap", "JPM", 0.25, "2026-11-13", None, status="scheduled")   # the book will hold only this one
    amd, jpm = mt.episodes(db)
    assert (amd["status"], amd["weight"], amd["exit_scheduled"]) == ("open", 0.2, "2026-11-12")
    assert (jpm["status"], jpm["weight"], jpm["usd"]) == ("scheduled", 0.25, None)
    assert len(mt.episodes(db)) == 2                                            # one card for JPM, not two


def test_scheduled_orders_and_zero_targets(db):
    order(db, "megacap_long", "JPM", 0.0, "2026-11-13", 346.0)                       # a zero target with no position: nothing
    order(db, "megacap_long", "AAPL", 0.25, "2026-11-16", None, status="scheduled")   # ordered, fills at the close
    order(db, "megacap_long", "MSFT", 0.2, "2026-11-13", 523.0)
    order(db, "megacap_long", "MSFT", 0.0, "2026-11-16", None, status="scheduled")    # exit ordered, not filled yet
    order(db, "megacap_long", "VOID", 0.2, "2026-11-13", 10.0, status="void")
    got = {e["symbol"]: e for e in mt.episodes(db)}
    assert set(got) == {"AAPL", "MSFT"}
    assert got["AAPL"]["status"] == "scheduled" and got["AAPL"]["entry"] is None and got["AAPL"]["usd"] is None
    assert got["MSFT"]["status"] == "open" and got["MSFT"]["exit_scheduled"] == "2026-11-16"


def test_forecast_and_tested_record_are_read_defensively(tmp_path):
    d = tmp_path / "megacap_ext" / "decisions"
    d.mkdir(parents=True)
    (d / "2026-11-12.json").write_text(json.dumps({"positions": {"AMD": {"pred": 0.0125, "q_lo": -0.0065, "q_hi": 0.0118,
                                                                       "position": 1}, "BAD": {"pred": "x"}}}))
    (d / "2026-11-13.json").write_text("{not json")
    assert mt.forecast(tmp_path, "megacap_ext", "AMD", "2026-11-12") == {"pred": 0.0125, "q_lo": -0.0065, "q_hi": 0.0118}
    assert mt.forecast(tmp_path, "megacap_long", "AMD", "2026-11-12") == {"pred": 0.0125, "q_lo": -0.0065, "q_hi": 0.0118}
    (d / "2026-11-10.json").write_text(json.dumps({"positions": {"AMD": {"pred": 0.01, "features": {"d_rv21": 0.074}},
                                                                 "JPM": {"pred": 0.01, "features": {"d_rv21": -1}}}}))
    assert mt.forecast(tmp_path, "megacap_ext", "AMD", "2026-11-10") == {"pred": 0.01, "sigma": 0.074}
    assert mt.forecast(tmp_path, "megacap_ext", "JPM", "2026-11-10") == {"pred": 0.01}      # a bad swing is dropped
    for args in (("megacap_ext", "BAD", "2026-11-12"), ("megacap_ext", "AMD", "2026-11-13"),
                 ("megacap_ext", "AMD", "2026-11-14"), ("etf:B_6040", "AMD", "2026-11-12")):
        assert mt.forecast(tmp_path, *args) is None
    stats = {"AMD": {"long": {"n": 188, "win": 0.51, "avg": 0.006}, "short": {"n": "x"}}, "X": 3}
    assert mt.tested(stats, "AMD", "long") == {"n": 188, "win": 0.51, "avg": 0.006}
    assert mt.tested(stats, "AMD", "short") is None and mt.tested(stats, "X", "long") is None
    assert mt.tested(None, "AMD", "long") is None and mt.tested(stats, "ZZZ", "long") is None


def _bars(start, n, step, p0=100.0):
    import pandas as pd

    ts = pd.date_range(start, periods=n, freq=step, tz="UTC")
    px = [p0 + i * 0.5 for i in range(n)]
    return pd.DataFrame({"ts": ts, "open": px, "high": [p + 0.3 for p in px], "low": [p - 0.3 for p in px],
                         "close": [p + 0.1 for p in px], "volume": [1000.0] * n})


def test_book_view_closed_trade_uses_the_books_fills_and_marks_entry_and_exit(db):
    from sgt.ops import charts

    for d in ("2026-11-12", "2026-11-13"):
        nav(db, "megacap_ext", d, 100_000)
    order(db, "megacap_ext", "AMD", 0.13, "2026-11-12", 585.30)
    order(db, "megacap_ext", "AMD", 0.0, "2026-11-13", 534.06)
    e = mt.episodes(db)[0]
    bars = _bars("2026-11-12T14:30:00Z", 156, "5min")                      # two sessions' worth of 5-minute bars
    v = charts.book_view(e, bars, decided="2026-11-12T20:50:00+00:00", entered="2026-11-12T20:59:00+00:00",
                         exited="2026-11-13T03:25:00+00:00", timeframe="5Min", book="megacap 15",
                         forecast={"pred": 0.0125}, tested={"n": 188, "win": 0.51, "avg": 0.006})
    assert (v["kind"], v["book"], v["key"], v["side"], v["symbol"]) == ("model", "megacap 15", e["key"], "long", "AMD")
    assert v["official"] and v["ret"] == pytest.approx(e["net"]) and v["usd"] == pytest.approx(e["usd"])
    assert (v["entry"], v["exit"]) == (585.30, 534.06) and v["stop"] is None and v["target"] is None
    assert v["conf"] is None and v["signals"] is None and v["forecast"] == {"pred": 0.0125} and v["tested"]["n"] == 188
    assert [m["kind"] for m in v["markers"]] == ["alert", "buy", "sell"] and len(v["candles"]) > 50
    assert v["markers"][0]["text"] == "decided" and v["score"] is None        # the 15:50 decision, not an alert
    assert v["sessions"] == 1 and v["weight"] == 0.13 and v["timeframe"] == "5Min"
    json.dumps(v, allow_nan=False)                                          # page-safe


def test_book_view_open_short_is_marked_to_the_last_bar_as_an_estimate(db):
    from sgt.ops import charts

    nav(db, "megacap_ext", "2026-11-12", 100_000)
    order(db, "megacap_ext", "TSLA", -0.2, "2026-11-12", 400.0)
    e = mt.episodes(db)[0]
    bars = _bars("2026-11-12T14:30:00Z", 370, "5min", p0=350.0)             # runs into the next session, not marked yet
    v = charts.book_view(e, bars, decided="2026-11-12T20:50:00+00:00", entered="2026-11-12T20:59:00+00:00",
                         exited=None, timeframe="5Min", book="megacap 15")
    last = float(bars.close.iloc[-1])
    assert not v["official"] and v["status"] == "open" and v["exit"] == pytest.approx(last)
    assert v["ret"] == pytest.approx(-(last / 400.0 - 1))
    assert v["usd"] == pytest.approx(-0.2 * 100_000 * (last / 400.0 - 1) - 0.2 * 100_000 * C)
    assert [m["kind"] for m in v["markers"]] == ["alert", "short"]
    # bars that end inside the book's last mark add nothing: the card shows the book's own dollars, no estimate on top
    marked = charts.book_view(e, _bars("2026-11-12T14:30:00Z", 78, "5min", p0=350.0), decided="2026-11-12T20:50:00+00:00",
                              entered="2026-11-12T20:59:00+00:00", exited=None, timeframe="5Min", book="megacap 15")
    assert marked["usd"] == pytest.approx(e["usd"]) == pytest.approx(-0.2 * 100_000 * C)
    assert marked["ret"] is not None and not marked["official"]
    text = _bars("2026-11-12T14:30:00Z", 78, "5min", p0=350.0).assign(ts=lambda b: b.ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ"))
    naive = _bars("2026-11-12T14:30:00Z", 78, "5min", p0=350.0).assign(ts=lambda b: b.ts.dt.tz_localize(None))
    for bars in (text, naive):                       # bars as the API and the disk cache hand them over: same answer
        again = charts.book_view(e, bars, decided="2026-11-12T20:50:00+00:00", entered="2026-11-12T20:59:00+00:00",
                                 exited=None, timeframe="5Min", book="megacap 15")
        assert again["usd"] == pytest.approx(e["usd"])


def test_book_views_builds_cards_from_the_db_and_survives_a_symbol_with_no_bars(db, tmp_path, monkeypatch):
    import sys
    from datetime import datetime, timezone
    from pathlib import Path

    import httpx

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import trade_charts as tc

    for d in ("2026-11-12", "2026-11-13"):
        nav(db, "megacap_ext", d, 100_000)
    order(db, "megacap_ext", "AMD", 0.13, "2026-11-12", 585.30)
    order(db, "megacap_ext", "AMD", 0.0, "2026-11-13", 534.06)
    order(db, "megacap_ext", "NOBARS", 0.1, "2026-11-12", 10.0)
    order(db, "megacap_ext", "NOBARS", 0.0, "2026-11-13", 11.0)
    order(db, "megacap_long", "JPM", 0.25, "2026-11-16", None, status="scheduled")
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append((req.url.path, req.url.params["timeframe"]))
        if "NOBARS" in req.url.path:
            return httpx.Response(200, json={"bars": None})
        b = _bars("2026-11-12T14:30:00Z", 156, "5min")
        return httpx.Response(200, json={"bars": [{"t": t.isoformat(), "o": o, "h": h, "l": lo, "c": c, "v": v} for
                                                  t, o, h, lo, c, v in b.itertuples(index=False)]})

    monkeypatch.setattr(tc, "SPAN_CACHE", tmp_path / "spans")
    now = datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc)                # Saturday
    with httpx.Client(base_url="https://data.example", transport=httpx.MockTransport(handler)) as client:
        views = tc.book_views(db, client, now, reports=tmp_path)
    assert [v["symbol"] for v in views] == ["AMD"] and views[0]["book"] == "megacap 15" and views[0]["official"]
    assert ("/v2/stocks/AMD/bars", "5Min") in seen and views[0]["tested"] is None    # no per-stock expectation
    sc = views[0]["score"]                                                  # one tested number per side, in dollars
    assert (sc["kind"], sc["go"]) == ("model_long", True) and sc["usd"] == pytest.approx(0.13 * 100_000 * 0.0025)
    assert sc["swing"] is None                                              # no decision file here: no swing invented
    assert (tmp_path / "spans" / "5Min").exists()                           # a finished span is cached
