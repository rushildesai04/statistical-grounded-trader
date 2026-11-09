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
