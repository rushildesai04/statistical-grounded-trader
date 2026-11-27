"""The long book on an early-close day (2026-11-27, the bell at 13:00 ET): its candles, its last entry and its exits
follow the exchange calendar, the same one the short book's cutoff and exit use (owner 2026-11-26: "verify that entry
cutoffs, exits, monitoring, and deployment guards use the same exchange calendar")."""
from __future__ import annotations

import pandas as pd
import pytest

from sgt.fast import fade, long_book as lb, long_shadow
from sgt.ops.store import OpsStore

from tests.fast.test_long_book import bars, one_minute_setups  # noqa: F401

HALF, FULL = "2026-11-27", "2026-11-09"
CALM = [(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.6, 500)]
QUIET = (10.6, 10.7, 10.5, 10.6, 100)


def et(h, m, s=0, day=HALF):
    return pd.Timestamp(f"{day} {h:02d}:{m:02d}:{s:02d}", tz="America/New_York").tz_convert("UTC")


def run(tmp_path, first_bar, shape, ticks):
    db = OpsStore(tmp_path / "ops.db")
    posted = first_bar - pd.Timedelta(seconds=40)
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                  "weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  ("ABCD", HALF, "volume_spike", posted.isoformat(), "-1", 1, "pending", None, 0.05, posted.isoformat()))
    day, sent = bars(shape, start=first_bar), []
    for t in ticks:
        lb.tick(db, lambda sym, now: day, sent.append, now=t.to_pydatetime())
    with db.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM long_trades ORDER BY id")], sent


def hm(ts):
    return pd.Timestamp(ts).tz_convert("America/New_York").strftime("%H:%M")


def test_every_cutoff_of_both_books_reads_the_same_close():
    d = pd.Timestamp(HALF).date()
    assert hm(lb.default_close(HALF)) == "13:00" and fade.last_entry(d).strftime("%H:%M") == "12:59"
    assert hm(long_shadow._latest(HALF)) == "13:00"            # no long fill on a candle starting at or after it
    assert fade._exit_at(HALF, et(12, 59, 30).to_pydatetime())[0] == et(13, 0).to_pydatetime()
    assert hm(lb.default_close(FULL)) == "16:00" and fade.last_entry(pd.Timestamp(FULL).date()).strftime("%H:%M") == "15:44"


def test_the_candles_the_book_reads_end_at_the_calendars_close():
    day = bars([QUIET] * 6, start=et(12, 57))                  # 12:57 .. 13:02
    kept = lb.rth(day, et(13, 5).to_pydatetime())
    assert [hm(t) for t in kept.ts] == ["12:57", "12:58", "12:59"]             # 13:00 on is after the bell
    full = bars([QUIET] * 6, start=et(15, 57, day=FULL))       # a full day is unchanged: its close is 16:00
    assert [hm(t) for t in lb.rth(full, et(16, 5, day=FULL).to_pydatetime()).ts] == ["15:57", "15:58", "15:59"]
    mid = bars([QUIET] * 3, start=et(13, 0, day=FULL))
    assert len(lb.rth(mid, et(13, 5, day=FULL).to_pydatetime())) == 3          # 13:00 on a full day is session time


def test_a_long_open_at_an_early_close_leaves_at_the_last_session_candle(tmp_path):
    rows, sent = run(tmp_path, et(12, 51), CALM + [QUIET] * 30,
                     [et(12, 54) + pd.Timedelta(seconds=30 * k) for k in range(0, 14)])
    (r,) = rows
    assert (r["status"], r["reason"], hm(r["entry_ts"]), hm(r["est_exit_at"]), r["est_exit"]) == (
        "est_closed", "time", "12:53", "12:59", 10.6) and len(sent) == 2


def test_no_long_is_opened_on_a_candle_after_an_early_close(tmp_path):
    rows, sent = run(tmp_path, et(12, 59), CALM + [QUIET] * 30,
                     [et(13, 2) + pd.Timedelta(seconds=30 * k) for k in range(0, 8)])
    assert [r for r in rows if r["status"] in ("open", "est_closed")] == [] and sent == []


@pytest.mark.parametrize("ticks", [[(12, 58, 0), (13, 2, 10)], [(12, 58, 0), (13, 0, 5), (13, 2, 10)]])
def test_a_post_close_candle_never_closes_a_long_even_when_the_exit_check_is_late(tmp_path, ticks):
    """Reproduced 2026-11-26 before the fix: with no exit check between 12:58 and 13:02, the 13:00 free-feed candle
    (after the bell, a low under the stop) closed the long as a stop at 13:00. The session ended at 13:00: the long
    leaves at the last session candle's close, whenever the check runs."""
    post = CALM + [QUIET, QUIET, (10.6, 10.6, 10.0, 10.1, 900), (10.1, 10.2, 10.0, 10.1, 100)]   # 13:00: low 10.00
    rows, _ = run(tmp_path, et(12, 55), post, [et(*t) for t in ticks])
    (r,) = rows
    assert (r["status"], r["reason"], hm(r["est_exit_at"]), r["est_exit"]) == ("est_closed", "time", "12:59", 10.6)
