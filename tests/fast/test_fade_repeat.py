"""Amendment F8 (prereg_fade_repeat_days_2026-11-12): no short on a stock the channel alerted 1-7 days earlier."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from sgt.fast import fade, long_shadow
from sgt.ops import approvals as ap
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")


def at(day: str) -> datetime:
    return datetime.fromisoformat(f"{day}T10:00:00").replace(tzinfo=ET).astimezone(timezone.utc)


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def row(db, sym, session, status, reason=None):
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                  "created_at) VALUES (?,?,?,?,?,?,?,?,?)", (sym, session, "volume_spike", at(session).isoformat(), "-1",
                                                           hash((sym, session, status)) % 10**6, status, reason, "x"))


def alert(sym, day, mid=1):
    return fade.LiveAlert(sym, "volume_spike", at(day) - timedelta(seconds=10), "-1", mid, None)


def status_of(db, sym, session):
    with db.tx() as c:
        return c.execute("SELECT status, reason FROM fade_trades WHERE symbol=? AND session=? ORDER BY id DESC",
                         (sym, session)).fetchone()


def test_a_stock_alerted_yesterday_is_not_shorted_and_says_why(db):
    row(db, "RETO", "2026-11-11", "settled")
    ap.arm(db, at("2026-11-12"))
    assert fade.on_alert(db, alert("RETO", "2026-11-12"), now=at("2026-11-12")) == "filtered: repeat"
    assert tuple(status_of(db, "RETO", "2026-11-12")) == ("filtered", fade.REPEAT_REASON)


@pytest.mark.parametrize("prior,status,reason,repeat", [
    ("2026-11-05", "settled", None, True),                                # 7 days back: inside the window
    ("2026-11-04", "settled", None, False),                               # 8 days back: outside
    ("2026-11-11", "filtered", fade.NEWS_REASON, True),                   # a news alert was still an event
    ("2026-11-11", "filtered", fade.BETA_REASON, True),
    ("2026-11-11", "filtered", fade.REPEAT_REASON, True),                 # chains: a runner keeps being skipped
    ("2026-11-11", "skipped", "alert received late", True),
    ("2026-11-11", "skipped", fade.NO_PRINT_REASON, False),               # no entry bar: not a tested event
    ("2026-11-11", "skipped", "no quote within 5 min", False),            # the pre-11-09 wording of the same
    ("2026-11-11", "skipped", "session closed before entry", False),      # entry bar past the window
    ("2026-11-11", "skipped", "not approved in time", True),              # a real alert we chose not to trade
    ("2026-11-11", "void", "not a research event", False),
    ("2026-11-11", "filtered", "price 0.5000 outside $1-50", False),
    ("2026-11-11", "filtered", "outside 09:30-15:44", False),
])
def test_what_counts_as_a_prior_event(db, prior, status, reason, repeat):
    row(db, "RUNR", prior, status, reason)
    assert (fade.prior_alert(db, "RUNR", date(2026, 11, 12)) == prior) is repeat


def test_same_day_and_other_symbols_never_count(db):
    row(db, "AAA", "2026-11-12", "settled")
    row(db, "BBB", "2026-11-11", "settled")
    assert fade.prior_alert(db, "AAA", date(2026, 11, 12)) is None
    ap.arm(db, at("2026-11-12"))
    assert fade.on_alert(db, alert("CCC", "2026-11-12"), now=at("2026-11-12")).startswith("pending")


def test_the_long_book_still_trades_an_alert_the_short_skipped_as_a_repeat(db):
    row(db, "RETO", "2026-11-12", "filtered", fade.REPEAT_REASON)
    with db.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM fade_trades f WHERE " + long_shadow.QUALIFY, long_shadow.QUALIFY_ARGS
                      ).fetchone()[0]
    assert n == 1
