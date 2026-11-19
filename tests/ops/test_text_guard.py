"""Trade texts are never lost silently (owner 2026-11-18: every trade is texted, winners and losers)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from sgt.ops import text_guard as tg
from sgt.ops.store import OpsStore

NOW = datetime(2026, 11, 18, 16, 0, tzinfo=timezone.utc)        # 11:00 ET


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def events(db):
    with db.tx() as c:
        return [r[0] for r in c.execute("SELECT event FROM audit ORDER BY id")]


def test_a_text_under_the_limit_is_sent_and_counted(db):
    out = []
    assert tg.send_capped(db, out.append, "long_text", 3, "LONG OUT lost -2%", "long", NOW) == "sent"
    assert out == ["LONG OUT lost -2%"] and events(db) == ["long_text"]


def test_over_the_limit_is_recorded_and_the_first_one_says_so(db):
    out = []
    for k in range(2):
        tg.send_capped(db, out.append, "long_text", 2, f"t{k}", "long")
    assert tg.send_capped(db, out.append, "long_text", 2, "t2", "long") == "dropped"
    assert tg.send_capped(db, out.append, "long_text", 2, "t3", "long") == "dropped"
    assert out[:2] == ["t0", "t1"] and len(out) == 3 and "Text limit reached for long today (2)" in out[2]
    assert events(db) == ["long_text", "long_text", "long_text_dropped", "long_text_dropped"]


def test_a_failed_send_is_tried_twice_then_recorded(db):
    calls = []

    def flaky(text):
        calls.append(text)
        if len(calls) == 1:
            raise RuntimeError("twilio send HTTP 500")
    assert tg.send_capped(db, flaky, "fade_text", 5, "SHORT OUT", "short") == "sent" and len(calls) == 2

    def down(text):
        calls.append(text)
        raise RuntimeError("twilio send HTTP 500")
    assert tg.send_capped(db, down, "fade_text", 5, "SHORT IN", "short") == "failed" and len(calls) == 4
    assert events(db) == ["fade_text", "fade_text_failed"]


def test_a_failed_text_does_not_use_up_the_limit(db):
    def down(text):
        raise RuntimeError("x")
    for _ in range(5):
        tg.send_capped(db, down, "long_text", 2, "t", "long")
    out = []
    assert tg.send_capped(db, out.append, "long_text", 2, "real", "long") == "sent" and out == ["real"]


def test_each_book_has_its_own_count(db):
    out = []
    for k in range(3):
        tg.send_capped(db, out.append, "armed_text", 3, f"armed{k}", "armed long setup")
    assert tg.send_capped(db, out.append, "armed_text", 3, "armed3", "armed long setup") == "dropped"
    assert tg.send_capped(db, out.append, "long_text", 3, "LONG OUT lost", "long") == "sent"      # trade texts untouched


def test_a_failing_limit_notice_still_records_the_drop(db):
    def boom(text):
        raise RuntimeError("down")
    with db.tx() as c:
        c.execute("INSERT INTO audit(at, event, detail) VALUES (?,?,?)", (datetime.now(timezone.utc).isoformat(), "long_text", "{}"))
    assert tg.send_capped(db, boom, "long_text", 1, "t", "long") == "dropped" and "long_text_dropped" in events(db)


def test_digest_line_only_when_something_was_lost(db):
    assert tg.missed_line(db, date(2026, 11, 18)) is None
    with db.tx() as c:
        for ev, at in (("long_text_dropped", "2026-11-18T16:00:00+00:00"), ("long_text_dropped", "2026-11-18T17:00:00+00:00"),
                       ("fade_text_failed", "2026-11-18T18:00:00+00:00"), ("long_text", "2026-11-18T18:00:00+00:00"),
                       ("long_text_dropped", "2026-11-17T18:00:00+00:00")):
            c.execute("INSERT INTO audit(at, event, detail) VALUES (?,?,?)", (at, ev, "{}"))
    line = tg.missed_line(db, date(2026, 11, 18))
    assert line.startswith("texts NOT delivered today: 1 fade text (send failed), 2 long text (over the daily limit)")
    assert tg.missed_line(db, date(2026, 11, 19)) is None


def test_the_listener_gives_armed_texts_their_own_count():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import fade_live as fl

    src = Path(fl.__file__).read_text()
    assert 'arm_send = CappedSms(db, "armed_text", MAX_ARMED_TEXTS_DAY' in src and "None, arm_send, rule_send)" in src
    assert 'rule_send = CappedSms(db, "trader_text", MAX_TRADER_TEXTS_DAY' in src       # the trader's rule: its own count too
    assert "None, long_send" not in src
