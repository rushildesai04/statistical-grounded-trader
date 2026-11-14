from __future__ import annotations

import unicodedata
from datetime import date, timedelta

import pytest

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
    assert f["usd"] == pytest.approx(0.05 * base * (0.10 - 0.04 - 0.05))
    text = plain(summary.build(db, DAY))
    assert "Thu Nov 5 summary" in text and "3 trades · 1 won, 2 lost" in text
    assert "DNUT -$205" in text and "(some estimated)" in text and "traded" in text


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
    assert lg["usd"] == pytest.approx(base * (0.05 * -0.04 + 0.015 * -0.095 + 0.05 * 0.05))
    text = plain(summary.build(db, DAY))
    assert "Longs (paper): 3 trades · 1 won, 2 lost" in text and "SLSN -$200, FCUV -$142, LIVE +$250 (some estimated)" in text
    assert "megacap 15 -$1,000" in text and "megacap_ext" not in text                               # the digest's names
    # owner 2026-11-14: megacap 15 is a model test (measurement): shown, not in the total
    assert "Measurement only (not in the total): megacap 15 -$1,000" in text
    assert f"Paper total (trading books): {summary._usd(lg['usd'])}" in text
    with db.tx() as c:                                    # once all are official the long dollars equal the digest's
        c.execute("DELETE FROM long_trades WHERE symbol='LIVE'")
    assert summary.long_day(db, DAY)["usd"] == pytest.approx(digest.long_today(db, DAY)[1], abs=1.0)
