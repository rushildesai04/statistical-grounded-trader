"""Fade off (owner 2026-11-12 night, option A after the adversarial validation): no new shorts, nothing else changes.

An off row must be exactly an alert the fade would have shorted: the news, beta and price checks still classify first,
F8 still counts it as an alert, and the long book still trades it."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from sgt.fast import fade, long_book, long_shadow
from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")
T10 = datetime(2026, 11, 2, 10, 0, tzinfo=ET).astimezone(timezone.utc)


@pytest.fixture()
def db(tmp_path):
    d = OpsStore(tmp_path / "ops.db")
    ap.arm(d, T10)
    return d


def last(db):
    with db.tx() as c:
        return dict(c.execute("SELECT * FROM fade_trades ORDER BY id DESC LIMIT 1").fetchone())


def run(db, *, px=4.0, alert_type="volume_spike", beta=None, sym="ABCD", at=T10):
    sent = []
    fade.on_alert(db, fade.LiveAlert(sym, alert_type, at - timedelta(seconds=10), "-1", 1, None), now=at)
    fade.monitor(db, sent.append, lambda s, **k: px, now=at + timedelta(seconds=20),
                 beta=(lambda s, t: beta) if beta is not None else None)
    return sent


def test_off_means_no_short_and_no_text(db):
    db.set_flag(fade.OFF_FLAG, "1")
    assert run(db) == []
    r = last(db)
    assert (r["status"], r["reason"], r["est_entry"]) == ("filtered", fade.OFF_REASON, None)


@pytest.mark.parametrize("flag", [None, "0"])
def test_on_is_unchanged(db, flag):
    if flag is not None:
        db.set_flag(fade.OFF_FLAG, flag)
    assert not fade.is_off(db)
    sent = run(db)
    assert last(db)["status"] == "open" and len(sent) == 1


@pytest.mark.parametrize("flag", ["yes", "", "true", "1"])
def test_any_other_flag_value_is_off(db, flag):          # fail closed: no new shorts
    db.set_flag(fade.OFF_FLAG, flag)
    assert fade.is_off(db) and run(db) == [] and last(db)["reason"] == fade.OFF_REASON


def test_other_filters_still_classify_first(db):
    db.set_flag(fade.OFF_FLAG, "1")
    run(db, beta=3.4, sym="BETA")
    assert last(db)["reason"] == fade.BETA_REASON
    run(db, px=0.62, sym="PENY")
    assert last(db)["reason"].startswith("price 0.62")
    fade.on_alert(db, fade.LiveAlert("NEWS", "news", T10 - timedelta(seconds=10), "-1", 1, True), now=T10)
    assert last(db)["reason"] == fade.NEWS_REASON


def test_an_off_alert_still_counts_for_f8(db):
    db.set_flag(fade.OFF_FLAG, "1")
    run(db, sym="RUNR")
    assert fade.prior_alert(db, "RUNR", date(2026, 11, 3)) == "2026-11-02"
    db.set_flag(fade.OFF_FLAG, "0")                        # back on next day: the repeat is still skipped
    nxt = datetime(2026, 11, 3, 10, 0, tzinfo=ET).astimezone(timezone.utc)
    ap.arm(db, nxt)
    run(db, sym="RUNR", at=nxt)
    assert last(db)["reason"] == fade.REPEAT_REASON


def test_the_long_book_still_sees_an_off_alert(db):
    db.set_flag(fade.OFF_FLAG, "1")
    run(db, sym="LONG")
    assert fade.OFF_REASON in long_shadow.SHORT_SKIPS
    got = long_book.candidates(db, T10 + timedelta(minutes=1))
    assert [c["symbol"] for c in got] == ["LONG"] and got[0]["reason"] == fade.OFF_REASON


@pytest.mark.parametrize("flag, status", [("0", "ask"), ("1", "pending")])
def test_ask_first_never_asks_for_a_short_while_off(db, monkeypatch, flag, status):
    monkeypatch.setattr(fade, "ASK_FIRST", True)
    db.set_flag("fast_armed", "")                          # not ARMed: the ask-first branch is reachable
    assert not ap.armed(db, T10)
    db.set_flag(fade.OFF_FLAG, flag)
    fade.on_alert(db, fade.LiveAlert("ASKX", "volume_spike", T10 - timedelta(seconds=10), "-1", 1, None), now=T10)
    assert last(db)["status"] == status


def test_an_ask_row_from_before_the_switch_never_texts_a_request(db, monkeypatch):
    monkeypatch.setattr(fade, "ASK_FIRST", True)
    db.set_flag("fast_armed", "")
    fade.on_alert(db, fade.LiveAlert("OLDQ", "volume_spike", T10 - timedelta(seconds=10), "-1", 1, None), now=T10)
    assert last(db)["status"] == "ask"
    db.set_flag(fade.OFF_FLAG, "1")
    sent = []
    for k in (5, 20):                                      # ask -> pending, then the off check at the entry
        fade.monitor(db, sent.append, lambda s, **kw: 4.0, now=T10 + timedelta(seconds=k))
    assert sent == [] and (last(db)["status"], last(db)["reason"]) == ("filtered", fade.OFF_REASON)
