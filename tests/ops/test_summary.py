from __future__ import annotations

import json
import unicodedata
from datetime import date, timedelta

import pytest

from sgt.fast import est_costs
from sgt.ops import summary
from sgt.ops.store import OpsStore

DAY = date(2026, 11, 5)


@pytest.fixture
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def fade(db, symbol, status, net=None, entry=None, exit_=None, session=DAY.isoformat()):
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                  "weight, net, est_entry, est_exit, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (symbol, session, "new_high", "2026-11-05T15:00:00+00:00", "c", hash(symbol) % 1000, status,
                   0.05, net, entry, exit_, "2026-11-05T15:00:00+00:00"))


def plain(t: str) -> str:
    return unicodedata.normalize("NFKC", t)


def test_counts_money_and_traded_with_official_and_estimated_results(db):
    fade(db, "WIN", "settled", net=0.10)
    fade(db, "DNUT", "settled", net=-0.04)
    fade(db, "LATE", "est_closed", entry=10.0, exit_=10.5)              # not official yet: estimate
    fade(db, "SKIP", "skipped")                                         # never entered: not a trade
    fade(db, "OLD", "settled", net=0.5, session="2026-11-04")           # other day
    f = summary.fade_day(db, DAY)
    base = 100_000 * (1 + 0.05 * 0.5)                                   # book value after the earlier day
    assert f["n"] == 3 and f["wins"] == 1 and f["estimate"]
    assert f["traded"] == pytest.approx(3 * 0.05 * base)
    with db.tx() as c:
        at = c.execute("SELECT est_entry_at FROM fade_trades WHERE symbol='LATE'").fetchone()[0]
    cost = est_costs.cost("short", "LATE", at, 10.0)                    # 2026-11-16: the estimate is after est. costs
    assert cost > 0.009
    # 2026-11-24: dollars the way the book counts them: each official trade on the value the book had reached, the
    # estimate on the value after them (it was every trade on the evening-before value: $1 off the digest on 11-23)
    win = 0.05 * base * 0.10
    dnut = 0.05 * (base + win) * -0.04
    assert f["usd"] == pytest.approx(win + dnut + 0.05 * (base + win + dnut) * (-0.05 - cost))
    text = plain(summary.build(db, DAY))
    assert "Thu Nov 5 summary" in text and "3 trades · 1 won, 2 lost" in text
    assert "DNUT -$206" in text and "(some estimated)" in text and "traded" in text


def test_the_summary_and_the_digest_take_a_day_from_the_same_book_value(db):
    """2026-11-24: for 11-23 the digest said fade -$438 and the summary -$439, from the same three official results
    (BRAI -6.51%, BUUU -8.98%, SCAG -21.23% at 1.25% of the book each). The digest's figure is the change of the
    book's value, which compounds trade after trade; the summary took every trade on the evening-before value. Both
    now come from the book's value, to the cent, and the summary's per-trade dollars add up to it."""
    from sgt.fast import fade as fb
    from sgt.ops import digest

    nets = (("BRAI", -0.06507042957393482), ("BUUU", -0.08980605562130183), ("SCAG", -0.21233859999999993))
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, weight, net, "
                  "settled_at, created_at) VALUES ('OLD','2026-11-04','new_high','x','c',1,'settled',-0.3539,0.125,'2026-11-05T01:40','x')")
        for i, (sym, net) in enumerate(nets):
            c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, weight, net, "
                      "settled_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (sym, DAY.isoformat(), "new_high", "x", "c", 10 + i, "settled", 0.0125, net, "2026-11-06T01:40", "x"))
    base = fb.nav(db, through=DAY - timedelta(days=1))
    assert base == pytest.approx(95_576.25)                                    # about the book's value that evening
    f = summary.fade_day(db, DAY)
    book = fb.nav(db, DAY) - base
    assert f["usd"] == pytest.approx(book, abs=1e-6) and digest.fade_today(db, DAY)[1] == pytest.approx(book, abs=1e-9)
    assert round(book) == -438 and round(sum(0.0125 * base * n for _, n in nets)) == -439      # the two old figures
    assert sum(t["usd"] for t in f["trades"]) == pytest.approx(f["usd"]) and f["traded"] == pytest.approx(3 * 0.0125 * base)
    text = plain(summary.build(db, DAY))
    assert "0 won, 3 lost · -$438 ·" in text and "Paper total (trading books): -$438" in text


def test_quiet_day_says_so_and_books_are_included(db):
    with db.tx() as c:
        c.execute("INSERT INTO book_nav VALUES ('6040', ?, 101000, 0.01, '{}')", (DAY.isoformat(),))
    text = plain(summary.build(db, DAY))
    assert "Fades (paper): no trades" in text and "6040 +$1,000" in text and "Paper total (trading books): +$1,000" in text
    assert "Longs" not in text                                          # no long trades: no line
    assert "REAL" not in text                                           # pilot never ran: no real line


def test_chart_link_appears_only_when_configured(db, monkeypatch):
    assert "Charts" not in summary.build(db, DAY)
    monkeypatch.setenv("SGT_SECRET_CHARTS_TOKEN", "k" * 43)
    assert f"📈 Charts: https://sgt-runner.fly.dev/{'k' * 43}/" in summary.build(db, DAY)


def test_the_long_book_is_in_the_summary_and_in_the_total(db):
    """2026-11-13: the summary said -$823 and the digest -$1,164; the difference was the long book (-$341)."""
    from sgt.fast import long_book
    from sgt.ops import digest

    with db.tx() as c:
        c.execute("INSERT INTO book_nav VALUES ('megacap_ext', ?, 99000, -0.01, '{}')", (DAY.isoformat(),))
        for i, (sym, status, net, w, e0, e1) in enumerate((("SLSN", "settled", -0.04, 0.05, 1.27, 1.24),
                                                         ("FCUV", "settled", -0.095, 0.015, 6.2, 5.5),
                                                         ("VTSI", "void", None, 0.05, 3.82, 3.80),
                                                         ("LIVE", "est_closed", None, 0.05, 2.0, 2.1))):
            c.execute("INSERT INTO long_trades(fade_id, symbol, session, status, net, weight, est_entry, est_exit, "
                      "created_at, settled_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (i + 1, sym, DAY.isoformat(), status, net, w, e0, e1, "x",
                       f"{DAY.isoformat()}T21:0{i}:00+00:00" if status == "settled" else None))
    base = long_book.nav(db, through=DAY - timedelta(days=1))
    lg = summary.long_day(db, DAY)
    assert [t["symbol"] for t in lg["trades"]] == ["SLSN", "FCUV", "LIVE"] and lg["estimate"]      # void is no trade
    cost = est_costs.cost("long", "LIVE", None, 2.0)                    # no entry time on the row: the widest spread
    assert cost == pytest.approx(0.0125, abs=5e-5)
    slsn = base * 0.05 * -0.04
    fcuv = (base + slsn) * 0.015 * -0.095
    assert lg["usd"] == pytest.approx(slsn + fcuv + (base + slsn + fcuv) * 0.05 * (0.05 - cost))    # along the book
    text = plain(summary.build(db, DAY))
    assert "Longs (paper): 3 trades · 1 won, 2 lost" in text and "SLSN -$200, FCUV -$142, LIVE +$187 (some estimated)" in text
    assert "megacap 15 -$1,000" in text and "megacap_ext" not in text                               # the digest's names
    # owner 2026-11-14: megacap 15 is a model test (measurement): shown, not in the total
    assert "Measurement only (not in the total): megacap 15 -$1,000" in text
    assert f"Paper total (trading books): {summary._usd(lg['usd'])}" in text
    with db.tx() as c:                                    # once all are official the long dollars equal the digest's
        c.execute("DELETE FROM long_trades WHERE symbol='LIVE'")
    assert summary.long_day(db, DAY)["usd"] == pytest.approx(digest.long_today(db, DAY)[1], abs=1e-6)   # was within $1


def recorder_file(tmp_path, lines):
    db = OpsStore(tmp_path / "reports" / "ops" / "ops.db")
    f = tmp_path / "reports" / "feed_shadow" / "daily.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("\n".join(x if isinstance(x, str) else json.dumps(x) for x in lines) + "\n")
    return db


def test_the_feed_recorders_frozen_check_is_in_the_summary_text(tmp_path, monkeypatch):
    """Owner 2026-11-27: the result is delivered through the report itself, not only a file somebody has to read."""
    from sgt.fast import feed_shadow

    assert summary.RECORDER_ENV == feed_shadow.ENV
    said = "⚠️ feed recorder 2026-11-05: timing OUTSIDE: longest pass (maximum); integrity inside. 1520 passes"
    db = recorder_file(tmp_path, [{"session": "2026-11-04", "report": "feed recorder 2026-11-04: timing inside; integrity inside."},
                                  "torn", {"session": DAY.isoformat(), "report": "an earlier run"},
                                  {"session": DAY.isoformat(), "report": said + "\nsecond line " + "x" * 2000}])
    line = summary.recorder_line(db, DAY)
    assert line.startswith("📡 " + said + " second line xxx") and "\n" not in line and len(line) == 2 + summary.RECORDER_CHARS
    text = summary.build(db, DAY).split("\n")
    assert text[-2] == line and "Paper total" in plain(text[-1])           # with the day's other lines, before the total
    (tmp_path / "reports" / "feed_shadow" / "OFF").write_text("2026-11-05T21:40 switched off by the nightly check\n")
    assert summary.recorder_line(db, DAY) == line + " [OFF file present: the recorder is off until a person removes it]"
    assert summary.recorder_line(db, date(2026, 11, 3)).endswith(                                    # no line, but it IS off
        "not measured: its nightly job wrote no line for this session [OFF file present: the recorder is off until a person removes it]")


def test_a_missing_recorder_result_is_said_as_not_measured_and_never_left_out(tmp_path, monkeypatch):
    db = recorder_file(tmp_path, [{"session": "2026-11-04", "report": "feed recorder 2026-11-04: inside"},
                                  {"session": "2026-11-06", "note": "no look was recorded for this session"}])
    assert summary.recorder_line(db, DAY) == ("📡 feed recorder 2026-11-05: not measured: its nightly job wrote no line "
                                              "for this session")
    assert summary.recorder_line(db, date(2026, 11, 6)) == (
        "📡 feed recorder 2026-11-06: not measured: the session's line carries no check (written before the check was deployed)")
    (tmp_path / "reports" / "feed_shadow" / "daily.jsonl").unlink()
    monkeypatch.delenv(summary.RECORDER_ENV, raising=False)
    assert summary.recorder_line(db, DAY) == "" and "feed recorder" not in summary.build(db, DAY)   # never switched on
    monkeypatch.setenv(summary.RECORDER_ENV, "1")
    assert summary.recorder_line(db, DAY) == "📡 feed recorder 2026-11-05: not measured: its nightly job has written no line"
    monkeypatch.setattr(summary, "recorder_line", lambda *a: 1 / 0)
    assert "📡 feed recorder: not measured: the check could not be read" in summary.build(db, DAY)
