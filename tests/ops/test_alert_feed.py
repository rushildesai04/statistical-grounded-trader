"""Every alert of a day and what the agent did with it (owner 2026-11-16: a no-trade day was a blank page)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sgt.fast import fade
from sgt.ops import alert_feed, charts
from sgt.ops.store import OpsStore


def _alert(c, sym, session, hhmm_utc, status, reason=None, net=None, typ="volume_spike", mid=[0]):
    mid[0] += 1
    return c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                     "net, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (sym, session, typ, f"{session}T{hhmm_utc}:00+00:00", "-1", mid[0], status, reason, net, "x")).lastrowid


def _long(c, fid, sym, session, status, net=None):
    c.execute("INSERT INTO long_trades(fade_id, symbol, session, status, net, created_at) VALUES (?,?,?,?,?,?)",
              (fid, sym, session, status, net, "x"))


def _db(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    with db.tx() as c:
        d = "2026-11-16"
        _alert(c, "EARLY", d, "12:18", "filtered", "outside 09:30-15:44")
        news = _alert(c, "NEWS", d, "14:30", "filtered", fade.NEWS_REASON)
        _long(c, news, "NEWS", d, "none")
        off = _alert(c, "OFFD", d, "14:45", "filtered", fade.OFF_REASON)
        _long(c, off, "OFFD", d, "missed")
        _alert(c, "DRY", d, "15:10", "filtered", fade.BORROW_NONE)
        _alert(c, "HALT", d, "15:42", "skipped", fade.NO_PRINT_REASON)
        won = _alert(c, "WON", d, "16:00", "settled", "auto", net=0.0312)
        _long(c, won, "WON", d, "settled", net=-0.021)
        _alert(c, "LIVE", d, "17:30", "open", fade.VETO)
        _alert(c, "ODD", d, "18:00", "skipped", "daily cap 99 (owner 2026-11-09 said so)")
        _alert(c, "OLD", "2026-11-13", "15:00", "settled", "auto", net=-0.05)
    return db


def test_one_line_per_alert_newest_first_in_plain_words(tmp_path):
    rows = alert_feed.rows(_db(tmp_path))
    today = [r for r in rows if r["session"] == "2026-11-16"]
    assert [r["symbol"] for r in today] == ["ODD", "LIVE", "WON", "HALT", "DRY", "OFFD", "NEWS", "EARLY"]
    by = {r["symbol"]: r for r in rows}
    assert by["EARLY"]["at"] == "07:18" and by["WON"]["at"] == "11:00"            # Eastern time (EST, UTC-5)
    assert (by["EARLY"]["short"], by["EARLY"]["why"]) == ("not shorted", "outside the rule's hours (09:30 to 15:44 ET)")
    assert by["NEWS"]["why"] == "company news today: fades on news names only break even"
    assert by["NEWS"]["long"] == "no long setup filled" and not by["NEWS"]["traded"]
    assert by["OFFD"]["why"] == "the fade was switched off at that time"
    assert by["OFFD"]["long"] == "long setup filled before the book was on"
    assert by["DRY"]["why"] == "no shares to borrow at IBKR: a real account could not short it"
    assert by["HALT"]["why"] == "no trade printed within 5 minutes (halted or no quote)"
    assert (by["WON"]["short"], by["WON"]["why"], by["WON"]["long"], by["WON"]["traded"]) == (
        "shorted +3.1%", "", "long taken -2.1%", True)
    assert (by["LIVE"]["short"], by["LIVE"]["why"], by["LIVE"]["traded"]) == ("short open", "", True)
    assert by["ODD"]["why"] == "daily cap 99"                                     # unknown reason: as stored, no code ref
    assert by["OLD"]["session"] == "2026-11-13" and by["OLD"]["short"] == "shorted -5.0%"
    assert by["EARLY"]["long"] == ""                                              # no long row: nothing claimed


def test_bounds_sessions_rows_and_untrusted_text(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    with db.tx() as c:
        for k in range(8):
            _alert(c, f"S{k}", f"2026-11-{k + 2:02d}", "15:00", "filtered", fade.NEWS_REASON)
        _alert(c, "X" * 40, "2026-11-09", "15:01", "skipped", "<script>alert(1)</script>" + "y" * 400, typ="t" * 90)
        c.execute("UPDATE fade_trades SET posted_at='not a time' WHERE symbol='S7'")
    rows = alert_feed.rows(db)
    assert {r["session"] for r in rows} == {f"2026-11-{d:02d}" for d in (5, 6, 7, 8, 9)}   # latest five sessions only
    big = next(r for r in rows if r["symbol"].startswith("X"))
    assert len(big["symbol"]) == 12 and len(big["type"]) == 40 and len(big["why"]) == alert_feed.MAX_TEXT
    assert next(r for r in rows if r["symbol"] == "S7")["at"] == ""              # a bad time is blank, not an error
    assert len(alert_feed.rows(db, limit=3)) == 3 and alert_feed.rows(OpsStore(tmp_path / "empty.db")) == []
    page = charts.render([], datetime(2026, 11, 9, 20, tzinfo=timezone.utc), alerts=rows)
    assert "<script>alert(1)" not in page and "\\u003cscript>alert(1)" in page     # can never end the data script


def test_the_page_carries_the_alerts_and_lists_a_day_with_no_trade(tmp_path):
    rows = alert_feed.rows(_db(tmp_path))
    page = charts.render([], datetime(2026, 11, 16, 20, tzinfo=timezone.utc), alerts=rows)
    data = json.loads(page.split("const DATA = ", 1)[1].split(";\n", 1)[0])
    assert [a["symbol"] for a in data["alerts"]][:2] == ["ODD", "LIVE"] and data["trades"] == []
    assert charts.render([], datetime(2026, 11, 16, 20, tzinfo=timezone.utc)).count('"alerts": []') == 1
    # the Fade tab has the section, fills it per day, and a day with alerts joins the day picker
    assert 'id="feed"' in page and 'id="feed-rows"' in page and "function feed(day)" in page
    assert "const seen = feed(day);" in page and "...feedDays" in page
    assert "none passed the rules" in page


def test_every_stored_reason_of_the_fade_has_plain_words():
    for r in (fade.NEWS_REASON, fade.BETA_REASON, fade.REPEAT_REASON, fade.OFF_REASON, *fade.BORROW_REASONS,
              fade.NO_PRINT_REASON):
        assert alert_feed.why(r) != r and "amendment" not in alert_feed.why(r)
    assert alert_feed.why(None) == "" and alert_feed.why("price $0.62 outside $1-50") == "price $0.62 outside $1-50"
