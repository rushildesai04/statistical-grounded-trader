from __future__ import annotations

from datetime import date

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
