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


def test_a_watched_long_and_a_no_win_skip_read_plainly():
    # owner 2026-11-17: measure-only longs are "seen", a setup whose target cannot beat costs is skipped
    from sgt.ops import alert_feed as af

    assert af.long_words("seen", None) == "long seen, not traded"
    assert af.long_words("seen", -0.025) == "long seen, not traded: would have made -2.5%"
    assert af.long_words("nowin", None) == "long setup skipped: its target could not beat costs"
    # owner 2026-11-20: a long not bought for its cost stays listed with what it would have made
    assert af.long_words("costly", None) == "long not bought: trading cost above 1%"
    assert af.long_words("costly", -0.016) == "long not bought: trading cost above 1%: would have made -1.6%"


def test_an_alert_not_shorted_shows_what_it_would_have_made_and_each_gate_is_summed(tmp_path):
    """Owner 2026-11-18 (option A): the gates stay in the money book; the site shows what each one stopped and what
    those alerts would have made. A measurement: a taken trade never carries one."""
    from sgt.fast import would_have

    db = _db(tmp_path)
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET wh_net=0.031, wh_entry=5.0 WHERE symbol='NEWS'")
        c.execute("UPDATE fade_trades SET wh_net=-0.2 WHERE symbol='DRY'")
        c.execute("UPDATE fade_trades SET wh_note=? WHERE symbol IN ('EARLY','HALT')", (would_have.NO_TRADE,))
        c.execute("UPDATE fade_trades SET wh_net=0.5 WHERE symbol='WON'")            # can never happen; never shown
        c.execute("UPDATE fade_trades SET weight=0.05 WHERE status='settled'")
        d = "2026-11-16"
        _alert(c, "NEWS2", d, "14:31", "filtered", fade.NEWS_REASON)
        c.execute("UPDATE fade_trades SET wh_net=-0.011 WHERE symbol='NEWS2'")
        for px in ("0.6321", "0.9100"):
            _alert(c, "P" + px[-2:], d, "15:20", "filtered", f"price {px} outside $1-50")
        c.execute("UPDATE fade_trades SET wh_net=0.04 WHERE symbol='P21'")
    by = {r["symbol"]: r for r in alert_feed.rows(db)}
    assert (by["NEWS"]["would"], by["NEWS"]["would_n"]) == ("+3.1%", 0.031) and by["DRY"]["would"] == "-20.0%"
    assert by["EARLY"]["would"] == "no result: no regular-hours trade within 5 minutes" and by["EARLY"]["would_n"] is None
    assert by["OFFD"]["would"] == "scored tonight"                                # not scored yet: said, not blank
    assert (by["WON"]["would"], by["WON"]["would_n"], by["LIVE"]["would"]) == ("", None, "")
    g = {x["gate"]: x for x in alert_feed.gates(db) if x["session"] == "2026-11-16"}
    news = g["company news today: fades on news names only break even"]
    size = fade.entry_notional(db)
    assert (news["n"], news["scored"], news["won"]) == (2, 2, 1) and abs(news["avg"] - 0.01) < 1e-12
    assert abs(news["usd"] - 0.02 * size) < 1e-9 and news["size"] == size
    price = g["price outside $1 to $50"]                                          # one gate, whatever the price was
    assert (price["n"], price["scored"], price["avg"]) == (2, 1, 0.04)
    hours = g["outside the rule's hours (09:30 to 15:44 ET)"]
    assert (hours["n"], hours["scored"], hours["avg"], hours["usd"]) == (1, 0, None, None)
    assert "shorted" not in " ".join(g) and all(x["session"] != "2026-11-13" or x["n"] for x in alert_feed.gates(db))
    assert alert_feed.gates(OpsStore(tmp_path / "empty.db")) == []
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET weight=NULL")                               # the size cannot be read: no dollars,
    assert {x["usd"] for x in alert_feed.gates(db)} == {None}                        # the list is still shown
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET weight=0.05 WHERE status='settled'")
    page = charts.render([], datetime(2026, 11, 16, 20, tzinfo=timezone.utc), alerts=alert_feed.rows(db), gates=alert_feed.gates(db))
    data = json.loads(page.split("const DATA = ", 1)[1].split(";\n", 1)[0])
    assert len(data["gates"]) == len(alert_feed.gates(db)) and 'id="gate-rows"' in page and "Would have made" in page
    assert "Not a trade and not in any total" in page and "DATA.gates" in page
    assert charts.render([], datetime(2026, 11, 16, 20, tzinfo=timezone.utc)).count('"gates": []') == 1


def test_channel_alerts_that_never_became_a_row_are_listed_and_change_nothing_else(tmp_path):
    """Owner 2026-11-19: nothing the channel sent may be unknown. Alert types outside the rules and news tags with no
    headline are listed from the channel's own history; nothing is written to the ops database."""
    db = _db(tmp_path)
    with db.tx() as c:
        mid = c.execute("SELECT message_id FROM fade_trades WHERE symbol='NEWS'").fetchone()[0]
        before = c.execute("SELECT COUNT(*) FROM fade_trades").fetchone()[0]
    heard = [{"symbol": "SEAT", "alert_type": "premarket_high", "posted_at": "2026-11-16 12:56:38", "has_news": None, "message_id": 901},
             {"symbol": "POAS", "alert_type": "news", "posted_at": "2026-11-16T14:43:13+00:00", "has_news": 0, "message_id": 902},
             {"symbol": "NEWS", "alert_type": "news", "posted_at": "2026-11-16T14:30:00+00:00", "has_news": 1, "message_id": mid},   # has a row
             {"symbol": "SAT", "alert_type": "new_high", "posted_at": "2026-11-14T15:00:00+00:00", "has_news": None, "message_id": 903},  # no session listed
             {"symbol": "X" * 40, "alert_type": "t" * 90, "posted_at": "2026-11-16T15:00:00+00:00", "has_news": None, "message_id": 904},
             {"symbol": "BAD", "alert_type": "new_high", "posted_at": "not a time", "has_news": None, "message_id": 905},
             {"symbol": "", "alert_type": "new_high", "posted_at": "2026-11-16T15:00:00+00:00", "has_news": None, "message_id": 906}]
    rows = alert_feed.rows(db, channel_alerts=heard)
    today = [r for r in rows if r["session"] == "2026-11-16"]
    assert [r["symbol"] for r in today] == ["ODD", "LIVE", "WON", "HALT", "DRY", "X" * 12, "OFFD", "POAS", "NEWS", "SEAT", "EARLY"]
    by = {r["symbol"]: r for r in rows}
    assert (by["SEAT"]["at"], by["SEAT"]["why"], by["SEAT"]["short"]) == ("07:56", "alert type not in the rules", "not shorted")
    assert by["POAS"]["why"] == "news tag with no headline attached: not in the rules" and not by["POAS"]["traded"]
    assert (by["SEAT"]["would"], by["SEAT"]["would_n"], by["SEAT"]["long"], by["SEAT"]["id"]) == ("", None, "", None)
    assert len(by["X" * 12]["type"]) == 40 and "SAT" not in by and "BAD" not in by and all("_t" not in r for r in rows)
    assert sum(r["symbol"] == "NEWS" for r in rows) == 1                               # an alert with a row is listed once
    assert alert_feed.rows(db) == [r for r in rows if r["id"] is not None]             # without the history: as before
    g = {x["gate"]: x for x in alert_feed.gates(db, channel_alerts=heard) if x["session"] == "2026-11-16"}
    assert (g["alert type not in the rules"]["n"], g["alert type not in the rules"]["scored"]) == (3, 0)
    assert "alert type not in the rules" not in {x["gate"] for x in alert_feed.gates(db)}
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM fade_trades").fetchone()[0] == before  # the ops database is not touched
    page = charts.render([], datetime(2026, 11, 16, 20, tzinfo=timezone.utc), alerts=rows)
    assert "alert type not in the rules" in page


def test_the_channel_history_is_read_only_and_a_missing_one_never_stops_the_page(tmp_path):
    import sqlite3

    import trade_charts as tc

    db = _db(tmp_path)
    assert tc.channel_alerts(db, tmp_path / "nothing.db") == []
    p = tmp_path / "sgt.db"
    with sqlite3.connect(p) as s:
        s.execute("CREATE TABLE crowd_alerts(channel TEXT, message_id INTEGER, idx INTEGER, symbol TEXT, alert_type TEXT, "
                  "posted_at TEXT, has_news INTEGER)")
        s.executemany("INSERT INTO crowd_alerts VALUES (?,?,?,?,?,?,?)",
                      [("-1", 1, 0, "SEAT", "premarket_high", "2026-11-16 12:56:38", None),
                       ("-1", 2, 0, "POAS", "news", "2026-11-16 14:43:13", 0),
                       ("other-channel", 3, 0, "ZZZ", "new_high", "2026-11-16 15:00:00", None)])
    got = tc.channel_alerts(db, p)
    assert [a["symbol"] for a in got] == ["POAS", "SEAT"] and set(got[0]) == {"symbol", "alert_type", "posted_at", "has_news", "message_id"}
    assert len(tc.channel_alerts(db, p, limit=1)) == 1
    (tmp_path / "broken.db").write_text("not a database")
    assert tc.channel_alerts(db, tmp_path / "broken.db") == []                        # logged, the page builds without it



def test_each_alert_carries_what_the_sources_said_and_unlisted_ones_carry_nothing(tmp_path):
    """Owner 2026-11-19: news, filings, StockTwits and Reddit are read for every alert and shown in the list."""
    db = OpsStore(tmp_path / "ops.db")
    with db.tx() as c:
        a = _alert(c, "SRC", "2026-11-19", "15:00", "filtered", fade.NEWS_REASON)
        _alert(c, "BARE", "2026-11-19", "15:05", "filtered", "outside 09:30-15:44")
        c.execute("UPDATE fade_trades SET ctx_news_own=1, news_kind='OFFERING', ctx_head=?, filing_8k=1, st_n60=41, rd_rank=7 "
                  "WHERE id=?", ("SRC prices $5M offering " + "x" * 300, a))
    got = {r["symbol"]: r for r in alert_feed.rows(db)}
    assert got["SRC"]["context"] == "news: offering · 8-K today · StockTwits 41/hr · Reddit #7"
    assert got["SRC"]["headline"].startswith("SRC prices $5M offering") and len(got["SRC"]["headline"]) == 160
    assert (got["BARE"]["context"], got["BARE"]["headline"]) == ("", "")
    extra = alert_feed.rows(db, channel_alerts=[{"symbol": "GHOST", "at": "2026-11-19T15:10:00+00:00", "type": "premarket_high"}])
    ghost = [r for r in extra if r["symbol"] == "GHOST"]
    assert all((r["context"], r["headline"]) == ("", "") for r in ghost)
