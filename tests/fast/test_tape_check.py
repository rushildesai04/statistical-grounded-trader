"""Official-tape stop check (owner 2026-11-17, decision 3A; MPU: the free feed never showed the print over the stop)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from sgt.fast import est_costs, fade, tape_check as tc
from sgt.fast.fade_rule import VETO, VETO_EXIT
from sgt.ops.store import OpsStore, iso

IN = datetime(2026, 11, 17, 15, 3, 11, tzinfo=timezone.utc)        # 10:03:11 ET, short 1.54 -> stop 1.848
STOP = 1.54 * 1.2


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def short(db, status="open", reason=VETO, exit_at=None, exit_px=None, sym="MPU", entry=1.54, mid=1):
    with db.tx() as c:
        return c.execute(
            "INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, weight,"
            " est_entry, est_entry_at, est_last, est_exit, est_exit_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sym, "2026-11-17", "gaining_momentum", iso(IN - timedelta(seconds=69)), "-1", mid, status, reason, 0.0125,
             entry, iso(IN), exit_px or entry, exit_px, None if exit_at is None else iso(exit_at), iso(IN))).lastrowid


def row(db, rid):
    with db.tx() as c:
        return dict(c.execute("SELECT * FROM fade_trades WHERE id=?", (rid,)).fetchone())


def tape(*bars_):
    """Official bars as (HH:MM ET, open, high)."""
    f = pd.DataFrame([{"ts": pd.Timestamp(f"2026-11-17 {hm}", tz="America/New_York").tz_convert("UTC"), "open": o,
                       "high": h} for hm, o, h in bars_])
    calls = []

    def bars(symbol, start, end):
        calls.append((symbol, start, end))
        return f
    bars.calls = calls
    return bars


MPU = tape(("10:03", 1.60, 2.50), ("10:04", 1.51, 1.58), ("10:37", 1.64, 1.81), ("10:54", 1.81, 2.05),
           ("10:55", 1.97, 2.01), ("11:03", 1.74, 1.75))


def at(hm):
    return pd.Timestamp(f"2026-11-17 {hm}", tz="America/New_York").tz_convert("UTC").to_pydatetime()


def test_first_cross_skips_the_entry_minute_and_fills_at_the_stop_or_the_gap_open():
    f = MPU("MPU", IN, at("11:30"))
    assert tc.first_cross(f, IN, at("11:30"), STOP) == (at("10:54"), pytest.approx(STOP))   # 10:03's 2.50 is not read
    assert tc.first_cross(f, IN, at("10:54"), STOP) is None                                 # only bars before `before`
    gap = tape(("10:10", 2.00, 2.10))("X", IN, at("11:00"))
    assert tc.first_cross(gap, IN, at("11:00"), STOP) == (at("10:10"), 2.00)               # opened over the stop
    assert tc.first_cross(None, IN, at("11:00"), STOP) is None
    assert tc.first_cross(pd.DataFrame({"ts": ["x"], "open": ["y"], "high": [None]}), IN, at("11:00"), STOP) is None
    assert tc.first_cross(pd.DataFrame({"ts": [at("10:10")], "close": [9.0]}), IN, at("11:00"), STOP) is None
    bad = pd.DataFrame({"ts": [at("10:10"), at("10:11")], "open": [-1.0, 3.0], "high": [9.0, 2.0]})   # not sane bars
    assert tc.first_cross(bad, IN, at("11:00"), STOP) is None


def test_an_open_short_is_closed_at_the_stop_once_the_official_tape_shows_it(db):
    rid = short(db)
    sent = []
    assert tc.check(db, sent.append, MPU, now=at("11:00")) == 0          # 10:54 is not 16 minutes old at 11:00
    assert row(db, rid)["status"] == "open" and MPU.calls[-1][2] == at("10:44")
    assert tc.check(db, sent.append, MPU, now=at("11:11")) == 1
    r = row(db, rid)
    assert (r["status"], r["reason"]) == ("est_closed", tc.LATE) and r["est_exit"] == pytest.approx(STOP)
    assert r["est_exit_at"] == iso(at("10:54"))
    cost = est_costs.cost("short", "MPU", iso(IN), 1.54)
    from sgt.ops.sms_style import bold

    assert len(sent) == 1 and bold("FADE OUT") in sent[0] and "official tape" in sent[0] and "🛑" in sent[0]
    assert bold(f"({-0.2 - cost:+.1%})") in sent[0] and "bought back $1.85" in sent[0]
    assert tc.check(db, sent.append, MPU, now=at("11:12")) == 0 and len(sent) == 1          # once


def test_a_timer_cover_after_an_official_stop_is_corrected_once(db):
    """MPU as it happened: covered 1.70 on the timer at 11:03, the official tape had crossed the stop at 10:54."""
    rid = short(db, status="est_closed", reason="60 min", exit_at=at("11:03"), exit_px=1.70)
    sent = []
    assert tc.check(db, sent.append, MPU, now=at("11:09")) == 0          # 10:54 not yet free at 11:09
    assert tc.check(db, sent.append, MPU, now=at("11:11")) == 1
    r = row(db, rid)
    assert (r["status"], r["reason"]) == ("est_closed", tc.LATE) and r["est_exit"] == pytest.approx(STOP)
    cost = est_costs.cost("short", "MPU", iso(IN), 1.54)
    (text,) = sent
    assert "CORRECTION" in text and "10:54 ET" in text and f"{-0.2 - cost:+.1%}" in text
    assert f"not the {(1.54 - 1.70) / 1.54 - cost:+.1%} texted earlier" in text and "$1.7 later" in text
    usd = (-0.2 - cost) * 0.0125 * fade.nav(db)
    assert f"(-${abs(usd):,.0f})" in text
    assert tc.check(db, sent.append, MPU, now=at("11:20")) == 0 and len(sent) == 1
    assert fade.day_est_pnl(db, "2026-11-17") == pytest.approx((-0.2 - cost) * 0.0125)     # the day's estimate follows


def test_a_cross_after_the_cover_or_a_live_stop_changes_nothing(db):
    late = tape(("11:10", 1.80, 2.20))                                                    # after the 11:03 cover
    rid = short(db, status="est_closed", reason="60 min", exit_at=at("11:03"), exit_px=1.70)
    sent = []
    assert tc.check(db, sent.append, late, now=at("11:40")) == 0 and row(db, rid)["reason"] == "60 min"
    stopped = short(db, status="est_closed", reason="stop", exit_at=at("10:55"), exit_px=1.90, sym="AAA", mid=2)
    assert tc.check(db, sent.append, MPU, now=at("11:15")) == 1                             # only the first row
    assert row(db, stopped)["reason"] == "stop" and row(db, stopped)["est_exit"] == 1.90


def test_the_owners_no_and_the_close_are_correctable_and_old_rows_are_left_alone(db):
    a = short(db, status="est_closed", reason=VETO_EXIT, exit_at=at("11:00"), exit_px=1.75, sym="AAA", mid=2)
    b = short(db, status="est_closed", reason="close", exit_at=at("11:02"), exit_px=1.75, sym="BBB", mid=3)
    old = short(db, status="est_closed", reason="60 min", exit_at=at("11:03"), exit_px=1.70, sym="OLD", mid=4)
    sent = []
    assert tc.check(db, sent.append, MPU, now=at("11:12")) == 3
    assert {row(db, x)["reason"] for x in (a, b, old)} == {tc.LATE}
    stale = short(db, status="est_closed", reason="60 min", exit_at=at("11:03"), exit_px=1.70, sym="ZZZ", mid=5)
    assert tc.check(db, sent.append, MPU, now=at("11:50")) == 0                             # past the recheck window
    assert row(db, stale)["reason"] == "60 min"


def test_one_bad_trade_or_a_dead_feed_never_stops_the_others(db, caplog):
    short(db, sym="BAD", mid=2)
    good = short(db, sym="MPU", mid=3)

    def bars(symbol, start, end):
        if symbol == "BAD":
            raise RuntimeError("feed down")
        return MPU(symbol, start, end)
    sent = []
    assert tc.check(db, sent.append, bars, now=at("11:11")) == 1
    assert row(db, good)["reason"] == tc.LATE and "official-tape check failed for fade" in caplog.text
    assert tc.check(db, sent.append, lambda *a: pd.DataFrame(), now=at("11:30")) == 0


def test_a_pending_no_is_expired_when_the_trade_is_closed_late(db):
    rid = short(db)
    with db.tx() as c:
        c.execute("INSERT INTO approvals(code, strategy, kind, period, summary, payload, created_at, expires_at, status)"
                  " VALUES ('A1', ?, 'fadeexit', 'p', 's', '{}', ?, ?, 'pending')",
                  (f"fade:{rid}", iso(IN), iso(IN + timedelta(hours=2))))
    tc.check(db, [].append, MPU, now=at("11:11"))
    with db.tx() as c:
        assert c.execute("SELECT status, result FROM approvals").fetchone()[:] == ("expired", "trade closed")


def test_the_listeners_official_bar_fetcher_asks_for_the_official_feed_and_tolerates_junk():
    import sys
    from pathlib import Path

    import httpx

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import fade_live as fl

    t0 = datetime(2026, 11, 17, 15, 4, tzinfo=timezone.utc)
    good = [{"t": (t0 + timedelta(minutes=i)).isoformat(), "o": 1.5 + i / 100, "h": 1.6 + i / 100, "l": 1.4, "c": 1.5,
             "v": 100} for i in range(5)]
    junk = [{"t": "x", "o": "bad"}, "junk", {"t": 5, "o": 1, "h": 2, "l": 1, "c": 1}, {"t": t0.isoformat(), "o": True,
                                                                                    "h": 2, "l": 1, "c": 1}]
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen.update(dict(req.url.params))
        if "DOWN" in req.url.path:
            raise httpx.ConnectTimeout("slow")
        if "LATE" in req.url.path:
            return httpx.Response(403, json={"message": "subscription does not permit querying recent SIP data"})
        return httpx.Response(200, json={"bars": good + junk} if "MPU" in req.url.path else ["no"])

    bars = fl.official_bars(httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler)))
    b = bars("MPU", t0, t0 + timedelta(minutes=10))
    assert len(b) == 5 and list(b.columns) == ["ts", "open", "high", "low", "close"] and b.ts.is_monotonic_increasing
    assert seen["feed"] == "sip" and seen["timeframe"] == "1Min" and seen["limit"] == "1000"
    assert bars("ODD", t0, t0).empty and bars("DOWN", t0, t0).empty and bars("LATE", t0, t0).empty
    # end to end with the check: the fetched bars close an open short at its stop
    assert tc.first_cross(b, t0 - timedelta(minutes=1), t0 + timedelta(minutes=10), 1.63) == (t0 + timedelta(minutes=3), 1.63)


def test_a_trade_younger_than_the_official_delay_costs_no_request(db):
    rid = short(db)
    feed = tape(("10:10", 2.00, 2.10))
    assert tc.check(db, [].append, feed, now=IN + timedelta(minutes=10)) == 0 and feed.calls == []
    assert tc.check(db, [].append, feed, now=IN + timedelta(minutes=30)) == 1 and len(feed.calls) == 1
    assert row(db, rid)["est_exit"] == 2.00
