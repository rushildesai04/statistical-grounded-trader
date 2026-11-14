from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from sgt.learning import live_monitor as lm
from sgt.ops import journal
from sgt.ops.store import OpsStore


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def add_fades(db, nets, session="2026-11-10"):
    with db.tx() as c:
        for i, n in enumerate(nets):
            c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                      "net, created_at) VALUES (?,?,?,?,?,?,?,?,?)", (f"S{i}", session, "new_high", "x", "c", i,
                                                                    "settled", n, "x"))


def test_quiet_when_live_matches_the_test(db):
    add_fades(db, [0.05, -0.20, 0.08, 0.03, 0.06, -0.04, 0.07, 0.02] * 4)
    r = lm.check_trade_book(db, lm.BOOKS[0])
    assert r["n"] == 32 and r["alarms"] == [] and r["mean"] > 0
    line = lm.lines([r])[0]
    assert line.startswith("alert fade (no-news): 32 live trades") and "tested +1.70%" in line


def test_drift_alarm_on_a_sustained_shortfall_and_texts_once(db):
    add_fades(db, [-0.20, -0.08, -0.12, 0.01, -0.20, -0.15] * 6)
    res = lm.check_all(db)
    assert res[0]["alarms"] and lm.lines(res)[0].startswith("! DRIFT alert fade (no-news)")
    first = lm.new_alarms(db, res, date(2026, 11, 20))
    assert len(first) == 1 and lm.new_alarms(db, res, date(2026, 11, 21)) == []


def test_pre_f6_trades_are_not_judged_against_the_no_news_rule(db):
    add_fades(db, [-0.20] * 30, session="2026-11-06")
    assert lm.check_trade_book(db, lm.BOOKS[0])["n"] == 0


def test_model_book_stop_rule(db):
    with db.tx() as c:
        for i, nav in enumerate([100_000, 98_000, 94_000]):
            c.execute("INSERT INTO book_nav(strategy, session, nav, ret, holdings) VALUES (?,?,?,?,?)",
                      ("megacap", f"2026-11-1{i}", nav, 0.0, "{}"))
    r = lm.check_model_book(db, "megacap")
    assert r["cum"] == pytest.approx(-0.06) and r["alarms"] and "stop" in r["alarms"][0]
    assert lm.check_model_book(db, "megacap_ext")["n"] == 0


def test_ai_test_queue_counts_until_reviewed(db):
    from datetime import datetime, timezone
    now = datetime(2026, 11, 10, tzinfo=timezone.utc)
    journal.add_insight(db, "m", "s", {"distilled": [{"insight": "a", "proposed_test": "t"},
                                                     {"insight": "b", "proposed_test": ""}]}, now)
    assert journal.pending_tests(db) == 1
    journal.mark_reviewed(db)
    assert journal.pending_tests(db) == 0


def add_longs(db, nets, rules="A2"):
    with db.tx() as c:
        base = c.execute("SELECT COUNT(*) FROM long_trades").fetchone()[0]          # fade_id is UNIQUE
        for i, n in enumerate(nets, start=base):
            c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, weight, net, created_at, rules) "
                      "VALUES (?,?,?,?,?,?,?,?,?)", (i, f"L{i}", "2026-11-13", "L4a", "settled", 0.05, n, "x", rules))


def test_long_book_judged_against_its_tested_rule(db):
    """Owner 2026-11-10 option A, rules A2 since 2026-11-12: the live paper long book is compared with what the test
    said for the adopted rule (-2.09%/trade); trades opened before A2 are not judged against it."""
    book = [b for b in lm.BOOKS if b.name == "long book (paper)"][0]
    assert book.expectancy == pytest.approx(-0.0209)
    add_longs(db, [-0.30] * 5, rules=None)                                            # before A2: ignored
    add_longs(db, [0.04, -0.03, -0.02, -0.05, 0.05, -0.04, -0.03, -0.02] * 4)       # about the tested mean
    r = lm.check_trade_book(db, book)
    assert r["n"] == 32 and r["alarms"] == []
    assert lm.lines([r])[0].startswith("long book (paper): 32 live trades") and "tested -2.09%" in lm.lines([r])[0]


def test_long_book_drift_alarm_when_far_worse(db):
    book = [b for b in lm.BOOKS if b.name == "long book (paper)"][0]
    add_longs(db, [-0.12, -0.10, -0.15, -0.09, -0.11] * 8)
    assert lm.check_trade_book(db, book)["alarms"]


# ---- 2026-11-12: a drift alarm halves the book's size for new trades (owner: learning must kick in) ------------------
def test_scale_is_one_until_an_alarm_and_fails_closed_on_a_bad_flag(db):
    assert lm.scale(db, "long book (paper)") == 1.0
    for raw in ("abc", "nan", "inf", "-0.5", "1.5", ""):
        db.set_flag(lm.SCALE_FLAG + "long book (paper)", raw)
        assert lm.scale(db, "long book (paper)") == lm.MIN_SCALE            # never raises size
    db.set_flag(lm.SCALE_FLAG + "long book (paper)", "0.5")
    assert lm.scale(db, "long book (paper)") == 0.5


def test_a_new_alarm_halves_the_book_once_and_floors_at_min_scale(db):
    from datetime import date
    res = [{"book": "long book (paper)", "n": 40, "alarms": ["CUSUM 5.20 > h=5 (first)"]}]
    msgs = lm.new_alarms(db, res, date(2026, 11, 13))
    assert lm.scale(db, "long book (paper)") == 0.5 and "cut to 50% (was 100%)" in msgs[0]
    assert lm.new_alarms(db, res, date(2026, 11, 14)) == [] and lm.scale(db, "long book (paper)") == 0.5   # same alarm
    for k in range(3):
        lm.new_alarms(db, [{"book": "long book (paper)", "n": 60, "alarms": [f"CUSUM 5.{k} > h=5 (later {k})"]}],
                      date(2026, 11, 20))
    assert lm.scale(db, "long book (paper)") == lm.MIN_SCALE                # 0.25 floor, never zero


def test_only_the_live_books_are_scaled(db):
    from datetime import date
    lm.new_alarms(db, [{"book": "scanner (experiment)", "n": 30, "alarms": ["CUSUM 6 > h=5"]}], date(2026, 11, 13))
    assert db.flag(lm.SCALE_FLAG + "scanner (experiment)") is None


def test_book_names_match_the_trading_code():
    from sgt.fast import fade, long_book
    names = {b.name for b in lm.BOOKS}
    assert fade.BOOK in names and long_book.BOOK in names and set(lm.AUTO_BOOKS) == {fade.BOOK, long_book.BOOK}


def test_guard_lines_announce_a_cut_once_then_keep_a_standing_line(db):
    """Review 2026-11-12 F-1: the cut text reached nobody; the digest now shows it, then a standing line."""
    from datetime import date
    res = [{"book": "long book (paper)", "n": 40, "alarms": ["CUSUM 5.20 > h=5 (first)"]},
           {"book": "scanner (experiment)", "n": 30, "alarms": ["CUSUM 6 > h=5"]}]
    first = lm.guard_lines(db, res, date(2026, 11, 13))
    assert len(first) == 1 and "cut to 50% (was 100%). Ask Claude to restore it." in first[0]
    assert lm.guard_lines(db, res, date(2026, 11, 14)) == [
        "long book (paper): drift guard holds new trades at 50% of size (ask Claude to restore)"]
    assert lm.guard_lines(db, [], date(2026, 11, 15))[0].endswith("(ask Claude to restore)")
    db.set_flag(lm.SCALE_FLAG + "long book (paper)", "1")
    assert lm.guard_lines(db, [], date(2026, 11, 16)) == []


# --- forward record (owner 2026-11-12 night, option A: the guard also reads the fade's forward research record) ---
FWD = Path(__file__).resolve().parents[2] / "reports" / "alert_fade" / "forward.csv"


@pytest.fixture()
def fdb(tmp_path):
    return OpsStore(tmp_path / "ops" / "ops.db")          # forward record at tmp_path / fade_challengers / shadow.csv


def write_forward(db, nets, column="net_champion"):
    p = lm.forward_path(db)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"symbol,session,{column}\n" + "".join(f"S{i},2026-11-0{1 + i % 9},{x}\n" for i, x in enumerate(nets)))
    return p


def test_forward_record_path_sits_next_to_the_ops_dir(fdb, tmp_path):
    assert lm.forward_path(fdb) == (tmp_path / "fade_challengers" / "shadow.csv").resolve()


def test_no_forward_record_yet_is_silent(fdb):
    r = lm.check_forward(fdb)
    assert r["n"] == 0 and lm.line(r) is None and lm.guard_lines(fdb, [r], date(2026, 11, 13)) == []


@pytest.mark.skipif(not FWD.exists(), reason="local forward record not present")
def test_real_november_record_alarms_and_cuts_the_fade_book(fdb):
    import csv
    # 2026-11-14: frozen at the sessions the decision was made on (through 11-12). The local record grows every night;
    # one good day (11-13: 7 alerts, +9.8% each) lifted the open-ended mean above -2% and failed the deploy gate.
    nets = [float(r["net"]) for r in csv.DictReader(FWD.open()) if r["session"] <= "2026-11-12"]
    assert len(nets) >= 61
    write_forward(fdb, nets)
    r = lm.check_forward(fdb)
    assert r["alarms"] and r["alarms"][-1].startswith("win rate") and r["mean"] < -0.02
    assert lm.line(r).startswith(f"! DRIFT fade forward record (every alert): {len(nets)} forward trades")
    msgs = lm.guard_lines(fdb, [r], date(2026, 11, 13))
    assert len(msgs) == 1 and "size for new trades of alert fade (no-news) cut to 50% (was 100%)" in msgs[0]
    assert lm.scale(fdb, "alert fade (no-news)") == 0.5 and lm.scale(fdb, "long book (paper)") == 1.0
    assert lm.guard_lines(fdb, [r], date(2026, 11, 14)) == [   # same alarm: no second cut, a standing line
        "alert fade (no-news): drift guard holds new trades at 50% of size (ask Claude to restore)"]


def test_forward_record_matching_the_test_is_quiet(fdb):
    write_forward(fdb, [0.04, -0.03, 0.05, 0.02, -0.06, 0.03, 0.01] * 10)     # win 71%, mean +0.86%
    r = lm.check_forward(fdb)
    assert r["n"] == 70 and r["alarms"] == [] and "tested +1.12%" in lm.line(r)


@pytest.mark.parametrize("body", ["symbol,session,other\nA,2026-11-02,0.1\n", "symbol,session,net_champion\nA,x,abc\n"])
def test_unreadable_forward_record_is_shown_and_never_cuts(fdb, body):
    p = lm.forward_path(fdb)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    r = lm.check_forward(fdb)
    assert r["error"].startswith("forward record unreadable") and r["alarms"] == []
    assert lm.line(r).startswith("! fade forward record (every alert): forward record unreadable")
    assert lm.guard_lines(fdb, [r], date(2026, 11, 13)) == [] and lm.scale(fdb, "alert fade (no-news)") == 1.0


def test_oversized_forward_record_is_refused(fdb, monkeypatch):
    write_forward(fdb, [0.01] * 50)
    monkeypatch.setattr(lm, "MAX_FORWARD_BYTES", 100)
    assert "unreadable (ValueError)" in lm.check_forward(fdb)["error"]


def test_check_all_includes_the_forward_record(fdb):
    write_forward(fdb, [0.01] * 5)
    assert any(r["book"] == lm.FORWARD.name and r["n"] == 5 for r in lm.check_all(fdb))
