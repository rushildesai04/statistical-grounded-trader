"""The base break (L8), the live long book's entry since 2026-11-19 (docs/findings/long_cases_2026-11-19.md): wait for
some minutes in a narrow range after the alert, buy the close above it and above VWAP, stop under the whole base."""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from sgt.fast import long_armed as la, long_book as lb, long_shadow as ls
from sgt.fast import patterns as pt
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
    assert lb.tick(db, feed(DAY), sent.append, now=at(13)) == 0 and trades(db) == []    # break closed, fill candle not
    assert lb.tick(db, feed(DAY), sent.append, now=at(14)) == 1
    (t,) = trades(db)
    assert (t["kind"], t["est_entry"], t["stop"], t["trigger"], t["status"], t["rules"]) == ("L8", 10.6, 10.25, None, "open", "B1")
    assert t["target"] == pytest.approx(11.3) and pd.Timestamp(t["entry_ts"]) == T1 + 13 * pt.BAR
    assert 0 < t["weight"] <= lb.PROBE_USD / 100_000 + 1e-12     # test size, cut further by the stop distance
    assert sent[0].startswith("[SGT] 🟢 LONG ABCD @ $10.60") and "Setup: base break · stop $10.25 (-3.3%)" in sent[0]
    assert "Tested on past alerts: -0.7%/trade over 506 trades" in sent[0]
    for k in range(29, 80):
        lb.tick(db, feed(DAY), sent.append, now=at(0, 30 * k))
    (t,) = trades(db)
    assert (t["status"], t["reason"], t["est_exit"]) == ("est_closed", "target", pytest.approx(11.3))
    assert len(sent) == 2 and sent[1].startswith("[SGT] 🟢 LONG OUT ABCD · made")


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
    for k in range(0, 2 * 13 + 1):
        assert lb.tick(db, feed(day), sent.append, now=(reopen + pd.Timedelta(seconds=30 * k)).to_pydatetime()) == 0
    assert trades(db) == []                                      # 13 minutes after the reopen: nothing bought
    assert lb.tick(db, feed(day), sent.append, now=(reopen + 14 * pt.BAR).to_pydatetime()) == 1
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


def test_the_heads_up_text_calls_the_base_break_when_its_candle_closes(db):
    alert_row(db)
    fills, arms = [], []
    lb.tick(db, feed(DAY), fills.append, now=at(12, 50), arm=arms.append)       # 15:13 candle not closed
    assert arms == []
    lb.tick(db, feed(DAY), fills.append, now=at(13, 10), arm=arms.append)       # closed 10 s ago
    assert len(arms) == 1 and fills == [] and trades(db) == []
    assert "base break: buy at market now, last $10.60" in arms[0] and "stop $10.25" in arms[0]
    row = {"id": 1, "symbol": "ABCD", "session": "2026-11-09", "posted_at": POSTED.isoformat()}
    _, lines, _ = la.group(DAY.iloc[:13], row, at(13, 10), ls._latest("2026-11-09"))
    assert [s.kind for s, _ in lines] == ["L8"]


def test_the_record_line_quotes_the_live_setups_tested_range(db):
    alert_row(db)
    for k in range(0, 2 * 50):
        lb.tick(db, feed(DAY), [].append, now=at(0, 30 * k))
    lb.settle(db, sip({"ABCD": DAY}), cost, now=NIGHT)
    line = lb.record_line(db)
    assert "tested -2.1% to -0.7%" in line and "test size $500" in line


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
    assert sent[0].endswith("\n\U0001F4F0 At the alert: no company news · StockTwits 12/hr")
    other = alert_row(db, "BARE")
    assert lb.tick(db, feed(DAY), sent.append, now=at(14, 15)) == 1 and other
    assert "At the alert" not in sent[-1]                        # nothing read: no line, the text still goes out
