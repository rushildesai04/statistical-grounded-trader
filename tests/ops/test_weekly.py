from __future__ import annotations

import json
from datetime import date

import pytest

from sgt.fast import fade
from sgt.ops import digest, weekly
from sgt.ops.sms_style import bold, italic
from sgt.ops.store import OpsStore


def _db(tmp_path):
    db = OpsStore(tmp_path / "ops.db")
    with db.tx() as c:
        for i, (sym, sess, typ, net, own) in enumerate((
                ("RAYA", "2026-11-06", "news", -0.2175, 1), ("STAK", "2026-11-06", "volume_increasing", -0.0498, 1),
                ("CRIS", "2026-11-04", "volume_spike", 0.2564, 0), ("OLD", "2026-10-30", "news", 0.5, 0))):
            c.execute("INSERT INTO fade_trades (symbol, session, alert_type, posted_at, channel, message_id, status, "
                      "reason, created_at, net, news_own, weight, settled_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (sym, sess, typ, f"{sess}T15:00:00+00:00", "-1", i, "settled", "x",
                       f"{sess}T15:00:00+00:00", net, own, 0.05, f"{sess}T21:0{i}:00+00:00"))
    return db


def nav_row(db, strategy, session, nav, ret, holdings):
    with db.tx() as c:
        c.execute("INSERT INTO book_nav(strategy, session, nav, ret, holdings) VALUES (?,?,?,?,?)",
                  (strategy, session, nav, ret, json.dumps(holdings)))


def test_weekly_review_text(tmp_path):
    db = _db(tmp_path)
    (tmp_path / "learning").mkdir()
    (tmp_path / "learning" / "shadow_summary.json").write_text(json.dumps({"line": "learning: 38/300 forward trades"}))
    text = weekly.build(db, date(2026, 11, 6), reports=tmp_path)
    lines = text.splitlines()
    before, after = fade.nav(db, date(2026, 11, 1)), fade.nav(db, date(2026, 11, 6))
    assert before == pytest.approx(fade.NAV0 * 1.025) and after < before          # last week's OLD is not this week's
    week = after - before
    assert lines[0] == (f"[SGT] \U0001F5D3\uFE0F {bold('Week of Nov 2')}: {bold(digest.usd(week))} this week "
                        f"{italic('(paper, trading books)')}")
    assert lines[1] == f"long calls +$0, fade {digest.usd(week)}, longs +$0"
    assert lines[2] == f"{italic('not in the total:')} model tests +$0, yardsticks +$0" and lines[3] == ""
    assert lines[4] == f"\U0001F4B5 {bold('THIS WEEK')}"
    assert lines[5] == (f"\U0001F534 {bold('fade')} {digest.usd(week)}, 3 trades, 1 won, average -0.4% per trade "
                        f"({italic('tested +1.1%')})")
    assert lines[6] == "   no-news 1 avg +25.6% \u00B7 news 2 avg -13.4%"
    assert lines[7] == "   best CRIS +25.6% \u00B7 worst RAYA -21.8%"
    assert lines[8] == f"\u26AA {bold('longs')} $0, no settled trades this week"
    assert f"\U0001F393 {bold('LEARNING')}" in lines and "\u2022 38/300 forward trades" in lines
    assert "OLD" not in text                                      # last week's trade is excluded
    assert lines[-1].startswith("\U0001F9ED Decisions come at 300 live trades") and lines[-2] == ""


def test_week_dollars_per_daily_book_and_the_switch_line(tmp_path):
    db = _db(tmp_path)
    nav_row(db, "megacap", "2026-10-30", 100_000, 0.0, {"NVDA": .2})             # last mark before the week
    nav_row(db, "megacap", "2026-11-03", 101_000, 0.01, {"NVDA": .2})
    nav_row(db, "megacap", "2026-11-05", 102_010, 0.01, {})
    nav_row(db, "megacap_ext", "2026-11-04", 99_000, -0.01, {"JPM": .25})        # first mark ever: start = nav / (1 + ret)
    nav_row(db, "megacap_overlay", "2026-10-29", 100_000, 0.0, {})               # flat, never marked this week
    nav_row(db, "etf:B_6040", "2026-10-30", 100_000, 0.0, {"SPY": .6})           # holds, no mark this week
    nav_row(db, "megacap_long", "2026-11-09", 150_000, 0.5, {"AMD": .1})         # next week: not counted
    with db.tx() as c:
        c.execute("INSERT INTO long_trades(fade_id, symbol, session, status, net, weight, created_at, settled_at) "
                  "VALUES (1,'SLSN','2026-11-06','settled',-0.04,0.05,'x','2026-11-06T21:00:00+00:00')")
    db.set_flag(fade.OFF_FLAG, "1")
    text = weekly.build(db, date(2026, 11, 6), reports=tmp_path)
    assert f"\U0001F7E2 {bold('megacap')} +$2,010 (+2.01%)" in text
    assert f"\U0001F534 {bold('megacap 15')} -$1,000 (-1.00%)" in text
    assert f"\u26AA {bold('megacap overlay')} $0, {italic('flat')} all week" in text
    assert f"\u23F8\uFE0F {bold('60/40')} {italic('no close this week')}" in text   # no dollars invented
    assert bold("megacap long calls") not in text                               # first mark is next week
    # megacap + megacap 15 are model tests (measurement), 60/40 a yardstick: shown, none of them in the total
    assert text.splitlines()[1].startswith("long calls +$0, fade ")
    assert text.splitlines()[2] == f"{italic('not in the total:')} model tests +$1,010, yardsticks +$0"
    assert italic("measurement only, not in the total:") in text and italic("yardsticks, not in the total:") in text
    assert f"\U0001F534 {bold('longs')} " in text and ", 1 trades, 0 won, average -4.0% per trade" in text
    assert f"\u23F8\uFE0F fade {italic('OFF')}: no new trades, still scored every night" in text


def test_a_tripped_monitor_is_flagged(tmp_path, monkeypatch):
    from sgt.learning import live_monitor

    monkeypatch.setattr(live_monitor, "check_all", lambda db: [])
    monkeypatch.setattr(live_monitor, "lines", lambda res: ["! DRIFT fade forward record: 73 trades", "megacap: 8 days"])
    text = weekly.build(_db(tmp_path), date(2026, 11, 6), reports=tmp_path)
    i = text.splitlines().index(f"\U0001F4CF {bold('LIVE VS TESTED')} {italic('(all trades so far)')}")
    assert text.splitlines()[i + 1:i + 3] == ["\U0001F6A8 DRIFT fade forward record: 73 trades", "\u2022 megacap: 8 days"]


def test_empty_week():
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        text = weekly.build(OpsStore(Path(d) / "ops.db"), date(2026, 11, 6), reports=Path(d))
    assert "no settled trades this week" in text and "+$0 this week".replace("+$0", bold("+$0")) in text


def test_the_weekly_headline_counts_the_long_calls_book_and_nothing_measured(tmp_path):
    db = OpsStore(tmp_path / "ops.db")                                           # owner 2026-11-14
    nav_row(db, "megacap_long", "2026-11-04", 100_500, 0.005, {"JPM": .25})      # trading: +$500
    nav_row(db, "megacap_ext", "2026-11-04", 99_000, -0.01, {"AMD": .13})        # measurement: -$1,000
    nav_row(db, "etf:B_6040", "2026-11-04", 99_700, -0.003, {"SPY": .6})         # yardstick: -$300
    lines = weekly.build(db, date(2026, 11, 6), reports=tmp_path).splitlines()
    assert f"{bold('+$500')} this week" in lines[0]
    assert lines[1] == "long calls +$500, fade +$0, longs +$0"
    assert lines[2] == f"{italic('not in the total:')} model tests -$1,000, yardsticks -$300"
    i, j = lines.index(italic("measurement only, not in the total:")), lines.index(italic("yardsticks, not in the total:"))
    assert bold("megacap long calls") in lines[i - 1] and bold("megacap 15") in lines[i + 1] and bold("60/40") in lines[j + 1]
