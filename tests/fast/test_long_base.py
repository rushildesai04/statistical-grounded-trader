"""The base break (L8), the live long book's entry since 2026-11-19 (docs/findings/long_cases_2026-11-19.md): wait for
some minutes in a narrow range after the alert, buy the close above it and above VWAP, stop under the whole base."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from sgt.fast import est_costs, fade, long_armed as la, long_book as lb, long_shadow as ls
from sgt.fast import patterns as pt
from sgt.ops.sms_style import bold, italic
from sgt.ops.store import OpsStore

POSTED = pd.Timestamp("2026-11-09 15:00:20", tz="UTC")          # 10:00:20 ET -> start bar 15:01
T1 = pd.Timestamp("2026-11-09 15:01", tz="UTC")
NIGHT = datetime(2026, 11, 10, 1, 40, tzinfo=timezone.utc)      # 20:40 ET: the settle job
COST = 0.01
SPIKE = (10.0, 10.5, 10.0, 10.4, 900)                           # 15:01, the alert's start bar
QUIET = (10.35, 10.45, 10.25, 10.35, 100)                       # a base candle: 10.25 to 10.45 (2.0%)
BREAK = (10.4, 10.65, 10.4, 10.6, 500)                          # closes above the base
FILL = (10.6, 10.7, 10.55, 10.65, 300)                          # the next candle: bought at its open 10.60
RUN = (10.65, 11.4, 10.6, 11.35, 800)                           # through the 2R target 11.30
AFTER = (11.3, 11.4, 11.2, 11.3, 100)


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def bars(rows, start=T1):
    return pd.DataFrame([{"ts": start + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                         for i, (o, h, lo, c, v) in enumerate(rows)])


def at_minutes(pairs):
    """Bars at chosen minutes after 15:01 (a thin feed has no bar for a quiet minute)."""
    return pd.DataFrame([{"ts": T1 + m * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                         for m, (o, h, lo, c, v) in pairs])


# 15:01 spike, 15:02-15:12 base, 15:13 break (ready 15:14), 15:14 fill at 10.60 (stop 10.25, target 11.30), 15:15 target
DAY = bars([SPIKE] + [QUIET] * 11 + [BREAK, FILL, RUN] + [AFTER] * 60)
# the same entry, but the fill candle closes red and the stock then drifts: no stop, no target
RED = bars([SPIKE] + [QUIET] * 11 + [BREAK, (10.6, 10.62, 10.45, 10.5, 300)] + [(10.5, 10.6, 10.45, 10.55, 100)] * 60)


def alert_row(db, sym="ABCD", posted=POSTED):
    with db.tx() as c:
        return c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status,"
                         " reason, weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (sym, "2026-11-09", "volume_spike", posted.isoformat(), "-1", hash(sym) % 10_000, "pending",
                          None, 0.05, posted.isoformat())).lastrowid


def trades(db):
    with db.tx() as c:
        return [dict(r) for r in c.execute("SELECT * FROM long_trades ORDER BY id")]


def at(minute, second=0):
    return (T1 + pd.Timedelta(minutes=minute, seconds=second)).to_pydatetime()


def feed(day):
    return lambda sym, now: day                                  # the full day: the book must use only closed bars


def sip(days):
    return lambda sym, session: days.get(sym, pd.DataFrame())


def cost(sym, ts, px):
    return COST


# ---- the detector -------------------------------------------------------------------------------------------------

def test_the_base_break_is_the_first_close_above_the_base_with_the_stop_under_all_of_it():
    (s,) = pt.base_break(DAY, T1)
    assert (s.kind, s.side, s.trigger, s.stop) == ("L8", "long", None, 10.25)
    assert s.ready == T1 + 13 * pt.BAR                           # the 15:13 candle's close: buy the 15:14 open
    assert s.info["base_high"] == 10.45 and s.info["break_ts"] == T1 + 12 * pt.BAR
    t = pt.simulate(DAY, s)
    assert (t.entry, t.entry_ts, t.reason, t.exit) == (10.6, T1 + 13 * pt.BAR, "target", pytest.approx(10.6 + 2 * 0.35))


def test_it_is_decided_from_closed_bars_only_and_never_changes_afterwards():
    (full,) = pt.base_break(DAY, T1)
    for k in range(len(DAY) + 1):
        got = pt.base_break(DAY.iloc[:k], T1)
        assert got == ([] if k <= 12 else [full]), k             # nothing before the break candle (index 12) is in


def test_a_wick_above_the_base_is_not_a_break():
    wick = (10.4, 10.65, 10.4, 10.44, 500)                       # trades above 10.45, closes under it
    assert pt.base_break(bars([SPIKE] + [QUIET] * 11 + [wick] + [QUIET] * 5), T1) == []


def test_the_base_must_be_narrow():
    wide = (10.35, 10.45, 10.00, 10.35, 100)                     # 10.00 to 10.45 = 4.5%
    ok = (10.35, 10.45, 10.10, 10.35, 100)                       # 10.10 to 10.45 = 3.5%
    assert pt.base_break(bars([SPIKE] + [wide] * 11 + [BREAK, FILL]), T1) == []
    (s,) = pt.base_break(bars([SPIKE] + [ok] * 11 + [BREAK, FILL]), T1)
    assert s.stop == 10.10 and pt.BASE_MAX_RANGE == 0.04


def test_the_break_must_also_be_above_session_vwap():
    """A stock that sold off hard on volume before the alert: a break of a small base under VWAP is not bought."""
    early = [(12.0, 12.1, 11.9, 12.0, v) for v in (0, 0, 0, 0, 0)]
    rows = [SPIKE] + [QUIET] * 11 + [BREAK, FILL]
    heavy = bars([(o, h, lo, c, 100_000) for o, h, lo, c, _ in early] + rows, start=T1 - 5 * pt.BAR)
    light = bars([(o, h, lo, c, 1) for o, h, lo, c, _ in early] + rows, start=T1 - 5 * pt.BAR)
    assert pt.base_break(heavy, T1) == []
    (s,) = pt.base_break(light, T1)
    assert s.info["vwap"] < 10.6 and s.ready == T1 + 13 * pt.BAR


def test_the_base_is_time_after_the_alert_not_before_it():
    """A stock that was quiet BEFORE the alert has not based after it: nothing in the first BASE_MIN."""
    quiet_start = (10.35, 10.45, 10.25, 10.35, 900)
    pre = [QUIET] * 20
    early = bars(pre + [quiet_start] + [QUIET] * 8 + [BREAK] + [(10.6, 10.65, 10.5, 10.6, 100)] * 30, start=T1 - 20 * pt.BAR)
    assert early.ts.iloc[29] == T1 + 9 * pt.BAR and pt.base_break(early, T1) == []      # break 9 minutes in: too soon
    on_time = bars(pre + [quiet_start] + [QUIET] * 9 + [BREAK, FILL], start=T1 - 20 * pt.BAR)
    (s,) = pt.base_break(on_time, T1)
    assert s.info["break_ts"] == T1 + pt.BASE_MIN


def test_the_break_must_come_within_the_watch():
    (s,) = pt.base_break(bars([SPIKE] + [QUIET] * 59 + [BREAK, FILL]), T1)
    assert s.info["break_ts"] == T1 + pt.BASE_WATCH
    assert pt.base_break(bars([SPIKE] + [QUIET] * 60 + [BREAK, FILL]), T1) == []


def test_a_thin_feed_needs_enough_candles_in_the_base_and_fills_on_the_next_one_it_gets():
    five = at_minutes([(0, SPIKE)] + [(m, QUIET) for m in (3, 5, 6, 8, 10)] + [(12, BREAK), (15, FILL), (16, RUN)])
    (s,) = pt.base_break(five, T1)
    assert s.ready == T1 + 13 * pt.BAR
    t = pt.simulate(five, s)
    assert (t.entry, t.entry_ts) == (10.6, T1 + 15 * pt.BAR)     # no candle at 15:14 or 15:15 on this feed: the next one
    four = at_minutes([(0, SPIKE)] + [(m, QUIET) for m in (3, 5, 6, 8)] + [(12, BREAK), (15, FILL), (16, RUN)])
    assert pt.base_break(four, T1) == []
    stale = at_minutes([(0, SPIKE)] + [(m, QUIET) for m in (3, 5, 6, 8, 10)] + [(12, BREAK), (19, FILL), (20, RUN)])
    (s,) = pt.base_break(stale, T1)
    assert pt.simulate(stale, s) is None                         # no candle within the order's life: no trade


def test_empty_bars_give_nothing():
    assert pt.base_break(DAY.iloc[:0], T1) == []


# ---- the live book ------------------------------------------------------------------------------------------------

def test_the_live_book_buys_the_base_break_and_says_so(db):
    alert_row(db)
    sent = []
    assert lb.tick(db, feed(DAY), sent.append, now=at(12, 55)) == 0 and trades(db) == []   # break candle not closed
    assert lb.tick(db, feed(DAY), sent.append, now=at(13, 5)) == 1                # closed 5 s ago: bought at its close
    (t,) = trades(db)
    assert (t["kind"], t["est_entry"], t["stop"], t["trigger"], t["status"], t["rules"]) == ("L8", 10.6, 10.25, None, "open", "B1")
    assert t["target"] == pytest.approx(11.3) and pd.Timestamp(t["entry_ts"]) == T1 + 13 * pt.BAR
    assert 0 < t["weight"] <= lb.PROBE_USD / 100_000 + 1e-12     # test size, cut further by the stop distance
    assert sent[0].startswith(f"[SGT] ✅ {bold('LONG IN')} · {bold('BUY ABCD')} @ {bold('$10.60')}")
    assert "🛑 stop $10.25 (-3.3%; a trigger, not a cap)" in sent[0]          # owner 2026-11-25: a stop does not cap a loss
    assert "\n⏱ Break candle closed 10:14:00 ET (5s ago): buy at market now" in sent[0]
    assert f"🎲 Past win rate {bold('37%')} · base break: " + italic("avg -0.3%/trade over 255 past trades") in sent[0]
    for k in range(29, 80):
        lb.tick(db, feed(DAY), sent.append, now=at(0, 30 * k))
    (t,) = trades(db)
    assert (t["status"], t["reason"], t["est_exit"]) == ("est_closed", "target", pytest.approx(11.3))
    assert len(sent) == 2 and sent[1].startswith(f"[SGT] 🟢 {bold('LONG OUT')} · {bold('ABCD')} · made")


# ---- the cost cap (owner 2026-11-20, decision A after SVRE; prereg_long_cost_cap_2026-11-20) -------------------------

LOW = DAY.assign(**{c: (DAY[c] * 0.3).round(4) for c in ("open", "high", "low", "close")})   # the same day at about $3


def test_a_fill_is_dear_when_its_estimated_cost_is_above_one_percent(monkeypatch):
    early, later = T1 + 13 * pt.BAR, T1 + 43 * pt.BAR            # 10:14 ET (the open hour) and 10:44 ET
    trade = lambda ts, px: pt.Trade("L8", "long", ts, px, ts, px, "time", 0.0)  # noqa: E731
    assert lb.costly("ABCD", trade(early, 3.18))                 # under $5 before 10:30: 1.25% round trip
    assert not lb.costly("ABCD", trade(later, 3.18))             # the same stock after 10:30
    assert not lb.costly("ABCD", trade(early, 10.6))             # $5 and up in the open hour
    for c, dear in ((0.01, False), (0.0101, True), (None, False)):          # at the cap is bought; no estimate: not judged
        monkeypatch.setattr(est_costs, "cost", lambda side, sym, at, px, c=c: c)
        assert lb.costly("ABCD", trade(early, 3.18)) is dear


def test_a_dear_long_is_not_bought_or_texted_and_is_scored_for_the_record(db):
    alert_row(db)
    sent, arms = [], []
    for k in range(0, 2 * 50):
        lb.tick(db, feed(LOW), sent.append, now=at(0, 30 * k), arm=arms.append)
    (t,) = trades(db)                                            # one row: the alert gets no other long (as tested)
    assert (t["kind"], t["status"], t["est_entry"], t["stop"]) == ("L8", "costly", pytest.approx(3.18), pytest.approx(3.075))
    assert sent == [] and arms == []                             # no text at all for a buy the book will not make
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM long_armed").fetchone()[0] == 0   # decided when the break candle closed
    assert pd.Timestamp(t["entry_ts"]) == T1 + 13 * pt.BAR
    assert lb.today_line(db, "2026-11-09") is None and lb.record_line(db) is None and lb.nav(db) == fade.NAV0
    assert lb.costly_line(db, "2026-11-09") == "longs not bought, cost above 1% (1; not in the total): ABCD base break"
    assert lb.cost_check_line(db) is None                        # nothing scored yet
    (line,) = lb.settle(db, sip({"ABCD": LOW}), cost, now=NIGHT)
    (t,) = trades(db)
    assert t["status"] == "costly" and t["settled_at"] is not None and t["entry"] == pytest.approx(3.18)
    assert t["net"] == pytest.approx(2 * (3.18 - 3.075) / 3.18 - COST)      # the 2R target on the official tape, after costs
    assert line == f"ABCD L8 not bought (cost above 1%): would have made {t['net']:+.2%}"
    assert lb.costly_line(db, "2026-11-09").endswith(f"ABCD base break {t['net']:+.1%}")
    assert lb.cost_check_line(db) == (f"long cost check (longs not bought, since 11-09, in no total): 1 at {t['net']:+.1%} "
                                      "(tested -1.6%; reread at 50)")
    assert lb.nav(db) == fade.NAV0 and lb.record_line(db) is None and lb.today_line(db, "2026-11-09") is None
    assert lb.settle(db, sip({"ABCD": LOW}), cost, now=NIGHT + timedelta(hours=1)) == []     # scored once


def test_the_same_low_priced_stock_is_bought_after_the_open_hour(db):
    posted = POSTED + pd.Timedelta(minutes=40)                   # 10:40 ET alert: the fill is at 10:54, cost 0.7%
    late = LOW.assign(ts=LOW.ts + pd.Timedelta(minutes=40))
    alert_row(db, posted=posted)
    sent = []
    assert lb.tick(db, feed(late), sent.append, now=at(54)) == 1          # the fill candle (10:54 ET) has closed
    (t,) = trades(db)
    assert t["status"] == "open" and t["est_entry"] == pytest.approx(3.18) and len(sent) == 1 and bold("BUY ABCD") in sent[0]


# ---- no long while our own short on the stock is open (owner 2026-11-20, JMIA; prereg_long_while_short_2026-11-20) --

def short_row(db, sym, status, session="2026-11-09"):
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, weight, "
                  "created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                  (sym, session, "new_high", POSTED.isoformat(), "-1", 90_000 + hash((sym, status, session)) % 9_000, status,
                   0.0125, POSTED.isoformat()))


@pytest.mark.parametrize("status, session, held", [("open", "2026-11-09", True), ("est_closed", "2026-11-09", False),
                                                   ("filtered", "2026-11-09", False), ("open", "2026-11-06", False)])
def test_the_short_counts_as_open_only_while_it_is_open_today(db, status, session, held):
    short_row(db, "ABCD", status, session)
    short_row(db, "WXYZ", "open")                                # another stock's short is no reason
    assert lb.short_open(db, "ABCD", "2026-11-09") is held


def test_a_long_is_not_bought_or_texted_while_our_short_on_the_stock_is_open(db):
    fid = alert_row(db)
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET status='open' WHERE id=?", (fid,))        # the short book took this alert
    sent, arms = [], []
    for k in range(0, 2 * 14 + 1):                               # up to the minute the base break fills
        lb.tick(db, feed(DAY), sent.append, now=at(0, 30 * k), arm=arms.append)
    (t,) = trades(db)
    assert (t["kind"], t["status"], t["est_entry"], t["stop"]) == ("L8", "short_open", 10.6, 10.25)
    assert sent == [] and arms == []                             # no armed text either: it will not be bought
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET status='est_closed' WHERE id=?", (fid,))   # the short closes: still no long
    for k in range(29, 60):
        lb.tick(db, feed(DAY), sent.append, now=at(0, 30 * k), arm=arms.append)
    assert [x["status"] for x in trades(db)] == ["short_open"] and sent == [] and arms == []
    assert lb.today_line(db, "2026-11-09") is None and lb.record_line(db) is None and lb.nav(db) == fade.NAV0
    assert lb.short_open_line(db, "2026-11-09") == "longs not bought, our short was open (1; not in the total): ABCD base break"
    assert lb.short_check_line(db) is None
    (line,) = lb.settle(db, sip({"ABCD": DAY}), cost, now=NIGHT)
    (t,) = trades(db)
    assert t["status"] == "short_open" and t["settled_at"] is not None and t["net"] == pytest.approx(11.3 / 10.6 - 1 - COST)
    assert line == f"ABCD L8 not bought (our short was open): would have made {t['net']:+.2%}"
    assert lb.short_check_line(db) == (f"long short-open check (longs not bought, since 11-09, in no total): 1 at {t['net']:+.1%} "
                                       "(tested -1.5%; reread at 50)")
    assert lb.nav(db) == fade.NAV0 and lb.cost_check_line(db) is None and lb.costly_line(db, "2026-11-09") is None
    assert lb.settle(db, sip({"ABCD": DAY}), cost, now=NIGHT + timedelta(hours=1)) == []


def test_a_long_is_bought_once_our_short_on_the_stock_has_closed(db):
    fid = alert_row(db)
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET status='est_closed' WHERE id=?", (fid,))  # short already out (stop or time)
    sent, arms = [], []
    for k in range(0, 2 * 14 + 1):
        lb.tick(db, feed(DAY), sent.append, now=at(0, 30 * k), arm=arms.append)
    (t,) = trades(db)
    assert t["status"] == "open" and arms == [] and len(sent) == 1 and bold("BUY ABCD") in sent[0]


def test_the_one_minute_setups_are_no_longer_bought_live(db):
    """A first red candle right after the alert (what the book bought until 2026-11-19, stopped in the entry minute
    45% of the time) opens nothing; with no base break the alert ends with the 'none' marker once ITS window is over."""
    legacy = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.65, 500),
                   (10.65, 11.6, 10.6, 11.5, 800)] + [(11.5, 11.6, 11.4, 11.5, 100)] * 120)
    found, _ = ls.setups(legacy, {"posted_at": POSTED.isoformat(), "status": "live"})
    assert {s.kind for s in found} >= {"L4a", "L4b"}             # the shadow still finds them (the nightly measurement)
    assert lb.live_setups(legacy, {"posted_at": POSTED.isoformat()}) == []
    alert_row(db)
    sent = []
    for k in range(0, 2 * 80):
        lb.tick(db, feed(legacy), sent.append, now=at(0, 30 * k))
    assert trades(db) == [] and sent == []                       # 80 minutes in: a base break could still come (halts)
    lb.tick(db, feed(legacy), sent.append, now=at(103))
    assert [(t["status"], t["kind"]) for t in trades(db)] == [("none", None)] and sent == []


def test_live_setups_are_only_the_live_kinds_and_need_a_start_bar():
    row = {"posted_at": POSTED.isoformat()}
    assert [s.kind for s in lb.live_setups(DAY, row)] == ["L8"]
    assert lb.live_setups(DAY.iloc[:0], row) == []
    assert set(lb.LIVE_KINDS) <= set(lb.LONG_KINDS) and all(k in lb.NAMES and k in lb.TESTED_N for k in lb.LIVE_KINDS)
    assert lb.LIVE_WATCH >= pt.BASE_WATCH and lb.LIVE_WATCH >= pt.WATCH


def test_the_window_stays_open_for_a_late_base_break(db):
    late = bars([SPIKE] + [QUIET] * 57 + [BREAK, FILL, RUN] + [AFTER] * 40)     # break 58 minutes after the start bar
    alert_row(db)
    sent = []
    for k in range(0, 2 * 59):
        assert lb.tick(db, feed(late), sent.append, now=at(0, 30 * k)) == 0
    assert trades(db) == []                                      # no 'none' marker in the meantime
    assert lb.tick(db, feed(late), sent.append, now=at(60)) == 1
    assert trades(db)[0]["kind"] == "L8"


def test_after_a_halt_the_base_is_built_from_the_reopen_and_the_reopen_red_candle_is_not_bought(db):
    """An alert into a halt: the start bar is the reopen. The halt-reopen red-candle setups (L7) are shadow-only now;
    the live book waits for a base after the reopen like after any alert."""
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason,"
                  " weight, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  ("HALT", "2026-11-09", "volume_spike", POSTED.isoformat(), "-1", 7, "skipped",
                   "no trade print within 5 min (halted or no quote)", 0.05, POSTED.isoformat()))
    reopen = POSTED.floor("min") + 9 * pt.BAR
    red = (10.5, 10.55, 10.2, 10.3, 400)                         # a red candle right after the reopen (the old L7 entry)
    day = bars([SPIKE, red] + [QUIET] * 10 + [BREAK, FILL, RUN] + [AFTER] * 40, start=reopen)
    found, start = ls.setups(day, {"posted_at": POSTED.isoformat(), "status": "live"})
    assert start == reopen and {s.kind for s in found} & {"L7a", "L7b"}
    sent = []
    for k in range(0, 2 * 13):
        assert lb.tick(db, feed(day), sent.append, now=(reopen + pd.Timedelta(seconds=30 * k)).to_pydatetime()) == 0
    assert trades(db) == []                                      # until the break candle closes: nothing bought
    assert lb.tick(db, feed(day), sent.append, now=(reopen + 13 * pt.BAR).to_pydatetime()) == 1
    (t,) = trades(db)
    assert (t["kind"], t["rules"], t["est_entry"], t["stop"]) == ("L8", "B1", 10.6, 10.25)
    assert pd.Timestamp(t["entry_ts"]) == reopen + 13 * pt.BAR


def test_a_base_break_long_has_no_red_candle_exit_live_or_in_the_official_result(db):
    """The red-candle exit (rules A2) was tested on the one-minute setups; the base break was tested without it."""
    alert_row(db)
    sent = []
    assert lb.tick(db, feed(RED), sent.append, now=at(14)) == 1                 # filled 10.60; that candle closes 10.50
    lb.tick(db, feed(RED), sent.append, now=at(16))
    (t,) = trades(db)
    assert (t["status"], t["rules"]) == ("open", lb.RULES_BASE) and lb.RULES_BASE != lb.RULES
    lb.tick(db, feed(RED), sent.append, now=(T1 + 13 * pt.BAR + pt.HOLD).to_pydatetime())
    (t,) = trades(db)
    assert (t["status"], t["reason"], t["est_exit"]) == ("est_closed", "time", 10.55)
    assert lb.settle(db, sip({"ABCD": RED}), cost, now=NIGHT) == [f"ABCD L8 {10.55 / 10.6 - 1 - COST:+.2%}"]
    (t,) = trades(db)
    assert (t["status"], t["reason"]) == ("settled", "time")


def test_the_official_result_replays_the_live_base_break_order_on_the_tape(db):
    alert_row(db)
    for k in range(0, 2 * 50):
        lb.tick(db, feed(DAY), [].append, now=at(0, 30 * k))
    tape = DAY.copy()
    tape.loc[13, "open"] = 10.62                                 # the official open of the fill minute differs a little
    (line,) = lb.settle(db, sip({"ABCD": tape}), cost, now=NIGHT)
    entry, target = 10.62, 10.62 + 2 * (10.62 - 10.25)
    assert line == f"ABCD L8 {target / entry - 1 - COST:+.2%}"   # the live stop, the 2R target from the official fill


# ---- bought when the break candle closes (owner 2026-11-20: "get in at the right time"; long_entry_timing_2026-11-20) --
THIN = at_minutes([(0, SPIKE)] + [(m, QUIET) for m in range(1, 12)] + [(12, BREAK), (16, FILL), (17, RUN)]
                  + [(m, AFTER) for m in range(18, 60)])          # the feed has no candle for three minutes after the break


def test_a_base_break_is_bought_when_its_candle_closes_and_needs_no_heads_up_text(db):
    alert_row(db)
    sent, arms = [], []
    lb.tick(db, feed(DAY), sent.append, now=at(12, 50), arm=arms.append)        # 15:13 candle not closed
    assert sent == [] and arms == [] and trades(db) == []
    assert lb.tick(db, feed(DAY), sent.append, now=at(13, 10), arm=arms.append) == 1      # closed 10 s ago
    (t,) = trades(db)
    assert (t["status"], t["est_entry"], t["stop"]) == ("open", 10.6, 10.25) and pd.Timestamp(t["entry_ts"]) == T1 + 13 * pt.BAR
    assert arms == [] and len(sent) == 1 and "(10s ago): buy at market now" in sent[0]
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM long_armed").fetchone()[0] == 0
    row = {"id": 1, "symbol": "ABCD", "session": "2026-11-09", "posted_at": POSTED.isoformat()}
    _, lines, _ = la.group(DAY.iloc[:13], row, at(13, 10), ls._latest("2026-11-09"))
    assert [s.kind for s, _ in lines] == ["L8"]                  # the order the heads-up text would have named


def test_the_buy_does_not_wait_for_a_feed_that_goes_quiet_and_the_official_price_is_that_minutes(db):
    alert_row(db)
    sent = []
    assert lb.tick(db, feed(THIN), sent.append, now=at(13, 5)) == 1               # no feed candle for 3 more minutes
    (t,) = trades(db)
    assert pd.Timestamp(t["entry_ts"]) == T1 + 13 * pt.BAR and t["est_entry"] == 10.6
    for k in range(27, 80):
        lb.tick(db, feed(THIN), sent.append, now=at(0, 30 * k))
    (t,) = trades(db)
    assert (t["status"], t["reason"], t["est_exit"]) == ("est_closed", "target", pytest.approx(11.3))
    tape = DAY.copy()
    tape.loc[13, "open"] = 10.5                                  # the full tape's first price of the minute the candle closed
    (line,) = lb.settle(db, sip({"ABCD": tape}), cost, now=NIGHT)
    (t,) = trades(db)
    assert t["entry"] == 10.5 and pd.Timestamp(t["fill_ts"]) == T1 + 13 * pt.BAR
    assert line == f"ABCD L8 {2 * (10.5 - 10.25) / 10.5 - COST:+.2%}"


def test_a_break_seen_late_is_bought_the_old_way_on_the_feeds_next_candle(db):
    alert_row(db)
    sent, arms = [], []
    assert lb.tick(db, feed(THIN), sent.append, now=at(13, 91), arm=arms.append) == 0     # first look 91 s after the close
    assert trades(db) == [] and sent == [] and arms == []
    with db.tx() as c:
        assert c.execute("SELECT note FROM long_armed").fetchone()["note"] == "seen late"
    assert lb.tick(db, feed(THIN), sent.append, now=at(17), arm=arms.append) == 1          # the feed's next candle closed
    (t,) = trades(db)
    assert pd.Timestamp(t["entry_ts"]) == T1 + 16 * pt.BAR and t["est_entry"] == 10.6 and "⏱" not in sent[0]


def test_exactly_ninety_seconds_after_the_close_is_still_bought_at_the_close(db):
    alert_row(db)
    assert lb.AT_CLOSE == pd.Timedelta(seconds=90)
    assert lb.tick(db, feed(THIN), [].append, now=at(13, 90)) == 1
    assert pd.Timestamp(trades(db)[0]["entry_ts"]) == T1 + 13 * pt.BAR


def test_no_buy_at_the_close_while_another_order_of_the_alert_is_pending(db, monkeypatch):
    alert_row(db)
    real = lb.live_setups
    flag = pt.Setup("L5v2", "long", T1 + 10 * pt.BAR, 11.0, 10.2)                # a buy-stop still alive at 15:14
    monkeypatch.setattr(lb, "live_setups", lambda f, row: [flag] + real(f, row))
    monkeypatch.setattr(pt, "TRIGGER_BARS", 10)
    sent, arms = [], []
    assert lb.tick(db, feed(DAY), sent.append, now=at(13, 5), arm=arms.append) == 0
    assert trades(db) == [] and sent == [] and len(arms) == 1    # the heads-up text names the pending orders instead
    assert lb.tick(db, feed(DAY), sent.append, now=at(14, 5), arm=arms.append) == 1        # first to fill: the base break
    (t,) = trades(db)
    assert (t["kind"], t["est_entry"]) == ("L8", 10.6) and pd.Timestamp(t["entry_ts"]) == T1 + 13 * pt.BAR and "⏱" not in sent[0]


def test_a_pending_buy_stop_is_never_bought_at_a_candle_close(db, monkeypatch):
    alert_row(db)
    flag = pt.Setup("L5v2", "long", T1 + 13 * pt.BAR, 11.0, 10.2)                # the alert's only order: a buy-stop
    monkeypatch.setattr(lb, "live_setups", lambda f, row: [flag])
    sent, arms = [], []
    assert lb.tick(db, feed(DAY), sent.append, now=at(13, 5), arm=arms.append) == 0
    assert trades(db) == [] and sent == [] and len(arms) == 1 and "buy-stop $11.00" in arms[0]


def test_a_buy_that_cannot_beat_its_cost_at_the_close_is_left_to_the_feeds_fill(db, monkeypatch):
    alert_row(db)
    monkeypatch.setattr(lb, "no_win", lambda sym, s, t: t.exit_ts == t.entry_ts)         # only the at-close buy is judged dead
    assert lb.tick(db, feed(DAY), [].append, now=at(13, 5)) == 0 and trades(db) == []
    assert lb.at_close(lb.rth(DAY, at(13, 5)), {"id": 1, "symbol": "ABCD", "session": "2026-11-09",
                                               "posted_at": POSTED.isoformat()}, at(13, 5), ls._latest("2026-11-09")) is None


def test_at_the_close_the_measure_only_book_sends_its_seen_text(db):
    alert_row(db)
    db.set_flag(lb.OFF_FLAG, "1")
    sent = []
    assert lb.tick(db, feed(DAY), sent.append, now=at(13, 5)) == 0
    (t,) = trades(db)
    assert t["status"] == "seen" and len(sent) == 1 and "LONG SEEN" in sent[0] and "(5s ago): buy at market now" in sent[0]


def test_a_feed_candle_that_opens_under_the_stop_after_the_buy_closes_it_there(db):
    gap = bars([SPIKE] + [QUIET] * 11 + [BREAK, (10.2, 10.3, 10.1, 10.2, 300)] + [AFTER] * 30)
    alert_row(db)
    sent = []
    assert lb.tick(db, feed(gap), sent.append, now=at(13, 5)) == 1
    lb.tick(db, feed(gap), sent.append, now=at(14, 5))
    (t,) = trades(db)
    assert (t["status"], t["reason"], t["est_exit"]) == ("est_closed", "stop", 10.2) and len(sent) == 2


def test_no_buy_at_the_close_once_the_last_entry_minute_has_passed(db):
    posted = pd.Timestamp("2026-11-09 20:32:20", tz="UTC")       # 15:32 ET alert: the break candle closes at 15:46 ET
    late = DAY.assign(ts=DAY.ts + (posted.floor("min") - POSTED.floor("min")))
    alert_row(db, posted=posted)
    now = (posted.floor("min") + 14 * pt.BAR + pd.Timedelta(seconds=5)).to_pydatetime()
    assert lb.tick(db, feed(late), [].append, now=now) == 0 and trades(db) == []


def test_the_record_line_quotes_the_live_setups_tested_range(db):
    alert_row(db)
    for k in range(0, 2 * 50):
        lb.tick(db, feed(DAY), [].append, now=at(0, 30 * k))
    lb.settle(db, sip({"ABCD": DAY}), cost, now=NIGHT)
    line = lb.record_line(db)
    assert "tested -2.1% to -0.3%" in line and "test size $500" in line


def test_a_crowded_day_reads_a_bounded_number_of_candidates_per_pass_and_starves_none(db, tmp_path, monkeypatch):
    """Each candidate costs one bar request a pass on a data key the short book shares: bounded, oldest read first."""
    monkeypatch.setattr(lb, "_looked", {})
    quiet = bars([SPIKE] + [QUIET] * 80)                         # no break: every alert stays a candidate
    n = lb.MAX_LOOKS
    names = [f"S{i:02d}" for i in range(n + 5)]
    for name in names:
        alert_row(db, name)
    asked = []
    look = lambda sym, now: (asked.append(sym), quiet)[1]        # noqa: E731
    lb.tick(db, look, [].append, now=at(5))
    assert asked == names[:n]                                    # never read yet: lowest ids first
    asked.clear()
    lb.tick(db, look, [].append, now=at(5, 15))
    assert asked == names[:n - 5] + names[n:]                    # the five that waited, then the longest unread
    asked.clear()
    lb.tick(db, look, [].append, now=at(5, 30))
    assert asked == names[:n] and set(names[n - 5:n]) <= set(asked)      # nobody waits more than one pass here
    few = OpsStore(tmp_path / "few.db")
    for name in names[:n]:
        alert_row(few, name)
    asked.clear()
    lb.tick(few, look, [].append, now=at(5))
    lb.tick(few, look, [].append, now=at(5, 15))
    assert asked == names[:n] * 2                                # at the bound: every candidate, every pass


def test_the_entry_text_says_what_the_sources_said_at_the_alert(db):
    """Owner 2026-11-19: news, filings, StockTwits and Reddit are read for every alert; the long text shows them."""
    fid = alert_row(db)
    with db.tx() as c:
        c.execute("UPDATE fade_trades SET ctx_news_own=0, ctx_news_earn=0, news_kind='NONE', st_n60=12 WHERE id=?", (fid,))
    sent = []
    assert lb.tick(db, feed(DAY), sent.append, now=at(14)) == 1
    assert sent[0].endswith("\n\U0001F4F0 At the alert: no company news · StockTwits 12/hr\n"      # the link stays last
                            "🔗 https://www.tradingview.com/chart/?symbol=ABCD")
    other = alert_row(db, "BARE")
    assert lb.tick(db, feed(DAY), sent.append, now=at(14, 15)) == 1 and other
    assert "At the alert" not in sent[-1]                        # nothing read: no line, the text still goes out
