"""What an alert the short book did not take would have made (owner 2026-11-18, option A). A measurement."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))   # runs alone too, not only in the full suite
from alert_fade_test import cover as research_cover  # noqa: E402
from sgt.fast import fade
from sgt.fast import would_have as wh
from sgt.fast.est_costs import LOCATE
from sgt.ops.store import OpsStore

DAY, NIGHT = "2026-11-18", datetime(2026, 11, 19, 1, 40, tzinfo=timezone.utc)
OPEN = pd.Timestamp("2026-11-18 14:30", tz="UTC")                # 09:30 ET
POSTED = pd.Timestamp("2026-11-18 15:00:20", tz="UTC")           # 10:00:20 ET -> first minute at or after +10 s: 15:01
COST = 0.01


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def day(path=None, flat=10.0, pre=True):
    """Official bars: 60 pre-market minutes, then the regular session; ``path``: minute-of-session -> (open, high)."""
    path, rows = path or {}, []
    if pre:
        rows += [{"ts": OPEN - pd.Timedelta(minutes=60 - i), "segment": "pre", "open": flat, "high": flat, "low": flat,
                  "close": flat, "volume": 100.0} for i in range(60)]
    for m in range(390):
        o, h = path.get(m, (flat, flat))
        rows.append({"ts": OPEN + pd.Timedelta(minutes=m), "segment": "rth", "open": o, "high": max(o, h), "low": o, "close": o,
                     "volume": 100.0})
    return pd.DataFrame(rows)


def alert(db, sym="AAA", status="filtered", reason=fade.NEWS_REASON, posted=POSTED, session=DAY, mid=[0]):
    mid[0] += 1
    with db.tx() as c:
        return c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                         "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (sym, session, "volume_spike", posted.isoformat(), "-1", mid[0], status, reason, "x")).lastrowid


def row(db, rid):
    with db.tx() as c:
        return dict(c.execute("SELECT * FROM fade_trades WHERE id=?", (rid,)).fetchone())


def run(db, days, now=NIGHT):
    return wh.run(db, lambda sym, session: days.get(sym), lambda sym, ts, px: COST, now=now)


def test_cover_is_the_research_cover():
    rng = np.random.default_rng(1)
    for _ in range(200):
        o = 10 * np.exp(np.cumsum(rng.normal(0, 0.03, 60)))
        w = pd.DataFrame({"open": o, "high": o * (1 + np.abs(rng.normal(0, 0.03, 60))), "close": o})
        assert wh.cover(w, float(o[0])) == research_cover(w, float(o[0]))
    assert wh.cover(pd.DataFrame(columns=["open", "high", "close"]), 10.0) is None


def test_the_short_is_the_rules_order_from_the_first_minute_after_the_alert():
    cost = lambda sym, ts, px: COST  # noqa: E731
    rth = day({31: (10.0, 10.0), 50: (9.0, 9.0)} | {m: (9.0, 9.0) for m in range(50, 200)}, pre=False)
    o = wh.short_from(rth, POSTED + fade.REACTION, "AAA", cost, not_after=POSTED + fade.MAX_ENTRY_DELAY)
    assert o["entry_ts"] == OPEN + pd.Timedelta(minutes=31) and o["entry"] == 10.0 and o["cover"] == 9.0
    assert o["gross"] == pytest.approx(0.10) and o["net"] == pytest.approx(0.10 - COST - LOCATE)
    stop = day({40: (10.0, 12.5)}, pre=False)
    assert wh.short_from(stop, POSTED, "AAA", cost)["gross"] == pytest.approx(-0.20)       # the 20% stop
    late = day({m: (9.0, 9.0) for m in range(91, 390)}, pre=False)                              # falls only after 60 minutes
    assert wh.short_from(late, POSTED, "AAA", cost)["gross"] == pytest.approx(0.0)
    gap = rth[rth.ts >= OPEN + pd.Timedelta(minutes=40)]                                        # halted: next trade 9 min later
    assert wh.short_from(gap, POSTED + fade.REACTION, "AAA", cost, not_after=POSTED + fade.MAX_ENTRY_DELAY) is None
    assert wh.short_from(gap, POSTED + fade.REACTION, "AAA", cost) is not None                  # no limit given: the next minute
    assert wh.short_from(rth.iloc[:0], POSTED, "AAA", cost) is None


def test_every_alert_not_taken_is_scored_once_and_a_taken_one_never(db):
    news = alert(db, "AAA")
    penny = alert(db, "PEN", reason="price 0.6321 outside $1-50")
    halt = alert(db, "HLT", status="skipped", reason=fade.NO_PRINT_REASON)
    early = alert(db, "PRE", reason="outside 09:30-15:44", posted=pd.Timestamp("2026-11-18 13:00:00", tz="UTC"))
    late = alert(db, "EOD", reason="outside 09:30-15:44", posted=pd.Timestamp("2026-11-18 20:50:00", tz="UTC"))
    taken = alert(db, "WON", status="open", reason="auto")
    days = {"AAA": day({m: (9.0, 9.0) for m in range(50, 390)}), "PEN": day({m: (0.54, 0.54) for m in range(50, 390)}, flat=0.6),
            "HLT": day().query("segment == 'pre'"), "PRE": day(), "EOD": day({m: (9.5, 9.5) for m in range(385, 390)}), "WON": day()}
    got = run(db, days)
    assert sorted(got) == sorted([f"AAA {0.10 - COST - LOCATE:+.1%}", f"PEN {0.10 - COST - LOCATE:+.1%}", "HLT no result",
                                  "PRE no result", f"EOD {0.05 - COST - LOCATE:+.1%}"])
    assert row(db, news)["wh_net"] == pytest.approx(0.10 - COST - LOCATE) and row(db, news)["wh_entry"] == 10.0
    assert row(db, penny)["wh_net"] == pytest.approx(0.10 - COST - LOCATE)                     # under $1: scored all the same
    assert (row(db, halt)["wh_net"], row(db, halt)["wh_note"]) == (None, wh.NO_TRADE)
    assert row(db, early)["wh_note"] == wh.NO_TRADE                                            # before the open: no result
    assert row(db, late)["wh_net"] == pytest.approx(0.05 - COST - LOCATE)                      # after the last entry: to the close
    assert (row(db, taken)["wh_net"], row(db, taken)["wh_note"]) == (None, None)
    assert run(db, {k: day({m: (1.0, 1.0) for m in range(390)}) for k in days}) == []          # written once
    assert row(db, news)["status"] == "filtered" and fade.nav(db) == fade.NAV0                  # no book value moves


def test_it_waits_for_complete_bars_and_gives_up_on_an_outage_after_a_few_days(db):
    a = alert(db, "AAA")
    assert run(db, {"AAA": day()}, now=datetime(2026, 11, 18, 20, 0, tzinfo=timezone.utc)) == []   # before the close
    assert run(db, {}) == [] and row(db, a)["wh_note"] is None                                  # no bars yet: retried
    assert run(db, {"AAA": pd.DataFrame()}, now=datetime(2026, 11, 25, 1, 40, tzinfo=timezone.utc)) == [f"AAA {wh.NO_BARS}"]
    old = alert(db, "OLD", session="2026-10-01", posted=pd.Timestamp("2026-10-01 15:00:20", tz="UTC"))
    assert run(db, {"OLD": day()}) == [] and row(db, old)["wh_net"] is None                     # outside the lookback


def test_a_tested_event_gets_exactly_the_research_result(tmp_path, monkeypatch, db):
    """The would-have of an alert that is a tested event equals the official scorer's net for that alert."""
    import fade_live as fl
    from sgt.intraday.calendar import default_calendar
    from sgt.intraday.store import ParquetBarStore

    store, cal = ParquetBarStore(tmp_path / "bars"), default_calendar()
    path = {m: (9.2, 9.3) for m in range(45, 70)} | {70: (9.2, 12.4)} | {m: (11.0, 11.0) for m in range(71, 390)}
    bars = [cal.annotate("AAA", (OPEN + pd.Timedelta(minutes=m)).to_pydatetime(), *(lambda o, h: (o, max(o, h), min(o, 10.0), o))(*path.get(m, (10.0, 10.0))), 1000)
            for m in range(390)]
    store.write_session("AAA", pd.Timestamp(DAY).date(), bars)
    monkeypatch.setattr(fl, "BARS", tmp_path / "bars")
    monkeypatch.setattr(fl, "backfill", lambda *a, **k: {})
    due = pd.DataFrame([{"id": 1, "symbol": "AAA", "alert_type": "volume_spike", "posted_at": POSTED.isoformat(), "session": DAY}])
    official = fl.official(due, now=NIGHT).iloc[0]
    assert official.qualifies and not official.why
    rid = alert(db, "AAA")
    _, cost = fl.sip_session_tools()
    wh.run(db, fl.sip_day_frames(), cost, now=NIGHT)
    assert row(db, rid)["wh_net"] == pytest.approx(official.net, abs=1e-12) and row(db, rid)["wh_entry"] == official.entry


# --- each gate against its own test (red-day review 2026-11-20) ---

def scored(db, sym, reason, net, session=DAY, status="filtered"):
    rid = alert(db, sym, status=status, reason=reason, session=session)
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET wh_net=?, wh_entry=1.0 WHERE id=?", (net, rid))
    return rid


def test_gates_count_a_stock_day_once_by_its_first_alert_and_only_the_choice_gates(db):
    scored(db, "AAA", fade.NEWS_REASON, -0.20)
    scored(db, "BBB", fade.NEWS_REASON, +0.10)
    scored(db, "CCC", fade.REPEAT_REASON, +0.30)
    scored(db, "CCC", fade.REPEAT_REASON, -0.90)                      # same stock, same day: the book fades the first only
    scored(db, "CCC", fade.REPEAT_REASON, -0.05, session="2026-11-17")
    scored(db, "DDD", "outside 09:30-15:44", -0.04)
    scored(db, "EEE", "price 0.4497 outside $1-50", +0.50)            # not a choice: never in the check
    scored(db, "FFF", "no shares to borrow at IBKR", +0.50)
    alert(db, "GGG", reason=fade.REPEAT_REASON)                       # not scored yet: not counted
    scored(db, "HHH", fade.REPEAT_REASON, +0.90, status="closed")     # a taken trade is not a blocked alert
    g = {x["gate"]: x for x in wh.gates(db)}
    assert set(g) == {"news", "hours", "repeat"}
    assert g["news"]["n"] == 2 and g["news"]["avg"] == pytest.approx(-0.05)
    assert g["repeat"]["n"] == 2 and g["repeat"]["avg"] == pytest.approx((0.30 - 0.05) / 2)
    assert g["repeat"]["since"] == "2026-11-17" and g["hours"]["tested"] is None
    assert not any(x["against"] for x in g.values())                  # two stock-days are never enough to mark a gate


def many(db, reason, nets):
    for i, x in enumerate(nets):
        scored(db, f"S{i:03d}", reason, x)


def test_a_gate_is_marked_only_when_its_stopped_shorts_win_by_more_than_chance(db):
    many(db, fade.REPEAT_REASON, [0.10, 0.12, 0.08, 0.11, 0.09])      # five stock-days, tight: lower bound above zero
    many(db, fade.NEWS_REASON, [0.40, -0.35, 0.30, -0.25, 0.05])      # average +3% but all noise: not marked
    g = {x["gate"]: x for x in wh.gates(db)}
    assert g["repeat"]["against"] and not g["news"]["against"] and g["news"]["avg"] == pytest.approx(0.03)
    line = wh.gates_line(db)
    assert line == ("gates check (shorts not taken, stock-days since 11-18, in no total): news 5 at +3.0%, "
                    f"repeat 5 at +10.0% (AGAINST the gate: tested -1.1%; reread at {wh.REREAD})")


def test_the_bound_uses_the_sample_spread(db):
    # mean +10%, sample sd 14.43%: lower bound 0.10 - 1.645 * 0.1443 / sqrt(5) = -0.6% -> not marked (the population
    # sd, 12.9%, would give +0.5% and mark it)
    many(db, fade.REPEAT_REASON, [-0.0443, 0.2443, -0.0443, 0.2443, 0.10])
    g = wh.gates(db)[0]
    assert g["avg"] == pytest.approx(0.10) and not g["against"]


def test_four_winning_stock_days_are_too_few_to_mark(db):
    many(db, fade.REPEAT_REASON, [0.10, 0.12, 0.08, 0.11])
    assert wh.gates_line(db).endswith("repeat 4 at +10.2%")
    assert wh.gates_line(OpsStore(db.path.parent / "empty.db")) is None        # nothing scored: no line


def test_gates_line_calls_the_reread_at_the_count(db, monkeypatch):
    monkeypatch.setattr(wh, "REREAD", 5)
    many(db, fade.BETA_REASON, [0.30, 0.10, 0.20, 0.25, 0.15])
    assert wh.gates_line(db).endswith("beta 5 at +20.0% (AGAINST the gate: tested -2.1%; reread now)")
    monkeypatch.setattr(wh, "REREAD", 6)
    assert wh.gates_line(db).endswith("reread at 6)")


def test_gates_read_only(db):
    rid = scored(db, "AAA", fade.NEWS_REASON, -0.20)
    before = row(db, rid)
    wh.gates_line(db)
    assert row(db, rid) == before
