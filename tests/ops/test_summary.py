from __future__ import annotations

import unicodedata
from datetime import date

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
    assert "Fades (paper): no trades" in text and "6040 +$1,000" in text and "Paper total: +$1,000" in text
    assert "REAL" not in text                                           # pilot never ran: no real line


def test_chart_link_appears_only_when_configured(db, monkeypatch):
    assert "Charts" not in summary.build(db, DAY)
    monkeypatch.setenv("SGT_SECRET_CHARTS_TOKEN", "k" * 43)
    assert f"📈 Charts: https://sgt-runner.fly.dev/{'k' * 43}/" in summary.build(db, DAY)
