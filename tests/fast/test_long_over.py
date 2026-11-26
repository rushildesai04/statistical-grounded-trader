"""A long whose exit is already determined when the book sees its fill is recorded, never opened (owner 2026-11-25
evening, decision B: "Trigger setup discovered after its opportunity is over: B - record-only. Do not book a position
retrospectively. Preserve the hypothetical replay separately and document the prospective effective version. Do not
remove historical observations or silently change registered sample membership.").

Owner 2026-11-26, confirming the scope: "Apply record-only handling to every long setup where its existing eligibility
window or trigger opportunity has demonstrably expired. Define 'already over' using each setup's specification. Do not
reject a still-valid setup merely because its qualifying candle completed earlier. Unknown cases need an explicit
reason, not retrospective booking." and "Keep the reason on the site and in the nightly counts, distinguishing
expired, invalidated, and insufficient-data cases."."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from sgt.fast import long_book as lb
from sgt.fast import patterns as pt
from sgt.ops import alert_feed
from sgt.ops.sms_style import bold
from sgt.ops.store import OpsStore

from tests.fast.test_long_book import DAY, NIGHT, POSTED, T1, alert_row, at, bars, cost, feed, one_minute_setups, sip, trades  # noqa: F401

# the same alert as DAY (red candle 15:02, buy-stop 10.55, stop 10.20, filled inside the 15:03 candle), three endings
RED = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.45, 500),
            (10.42, 10.5, 10.4, 10.45, 300)] + [(10.45, 10.5, 10.4, 10.45, 100)] * 50)      # fill candle closed under 10.55
STOPPED = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.1, 10.6, 500),
                (10.6, 10.7, 10.5, 10.6, 300)] + [(10.6, 10.7, 10.5, 10.6, 100)] * 50)      # fill candle's low under the stop
CALM = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.6, 500)]
            + [(10.6, 10.7, 10.5, 10.6, 100)] * 50)         # filled 15:03, closed above the entry, then nothing happens
TARGET = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 11.4, 10.3, 11.3, 500),
               (11.3, 11.4, 11.2, 11.3, 300)] + [(11.3, 11.4, 11.2, 11.3, 100)] * 50)       # fill candle reached 11.25


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


def decisions(db):
    with db.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM decisions WHERE book='long' ORDER BY id")]


def run(db, day, passes=9, sym="ABCD"):
    alert_row(db, sym)
    sent = []
    for k in range(0, passes):
        lb.tick(db, feed(day), sent.append, now=at(0, 30 * k))
    return sent


@pytest.mark.parametrize("day, exit_, px, cls, words", [
    (RED, "red", None, "invalidated",
     "its red-candle exit was already determined when the book saw the fill (out at the next candle's open)"),
    (STOPPED, "stop", 10.2, "invalidated",
     "its stop was already on a closed free-feed candle when the book saw the fill ($10.20, 10:03 ET candle)"),
    (TARGET, "target", 11.25, "expired",
     "its target was already on a closed free-feed candle when the book saw the fill ($11.25, 10:03 ET candle)")])
def test_a_fill_whose_exit_is_already_determined_is_recorded_and_never_opened(db, day, exit_, px, cls, words):
    sent = run(db, day, passes=140)                        # the whole window: nothing is opened later either
    (t,) = trades(db)
    assert (t["status"], t["kind"], t["est_entry"], t["stop"], t["trigger"]) == ("over", "L4b", 10.55, 10.2, 10.55)
    assert (t["est_exit"], t["est_exit_at"], t["entry_ref"], t["stop_seen"]) == (None, None, None, None)
    assert t["reason"] == words and t["created_at"] == at(3).isoformat()      # recorded when the fill candle closed
    assert sent == []                                      # no position: no LONG IN, no LONG OUT, no new text type
    (d,) = decisions(db)                                   # one decision line, with the version and what was seen
    facts = json.loads(d["facts"])
    assert (d["state_from"], d["state_to"], d["reason"]) == (None, "over", words)
    assert facts["entry_rule"] == lb.ENTRY_RULE and facts["entry_rule"].startswith("long-live-entry-v2 2026-11-25")
    over = json.loads(facts["over"])
    assert (over["exit"], over["exit_px"]) == (exit_, pytest.approx(px) if px else None) and facts["seen_after_s"] == 60.0
    why = json.loads(t["not_opened"])                      # the class and the cause, on the row and in the decision
    assert (why["version"], why["class"], why["cause"], why["exit"]) == ("long-live-entry-v2", cls, words, exit_)
    assert over["class"] == cls and json.loads(facts["not_opened"]) == why
    assert alert_feed.long_words("over", None, t["not_opened"]) == f"long not opened ({cls}): {words}"
    assert lb.nav(db) == 100_000.0                         # never in the book's value


def test_a_fill_whose_exit_is_still_open_is_opened_exactly_as_before_and_carries_the_version(db):
    sent = run(db, DAY)
    (t,) = trades(db)
    assert (t["status"], t["est_entry"], t["reason"]) == ("est_closed", 10.55, "target")       # 15:04 ran to the target
    assert bold("LONG IN") in sent[0] and bold("LONG OUT") in sent[1] and len(sent) == 2
    opened = [d for d in decisions(db) if d["state_to"] == "open"]
    assert len(opened) == 1 and json.loads(opened[0]["facts"])["entry_rule"] == lb.ENTRY_RULE


def test_over_is_what_the_exit_check_of_the_same_pass_would_have_done(db, monkeypatch):
    """The definition is the book's own exit code: without the rule, each of these was opened and then closed by the
    exit check of the same pass (stop, target) or left with nothing to decide (red)."""
    monkeypatch.setattr(lb, "already_over", lambda *a, **k: None)              # the book as it was before the rule
    for sym, day, reason in (("STOP", STOPPED, "stop"), ("TARG", TARGET, "target")):
        alert_row(db, sym)
        sent = []
        lb.tick(db, feed(day), sent.append, now=at(3))                         # ONE pass: the fill candle has closed
        (t,) = [x for x in trades(db) if x["symbol"] == sym]
        assert (t["status"], t["reason"]) == ("est_closed", reason) and len(sent) == 2     # in and out in one pass
    alert_row(db, "REDD")
    sent = []
    lb.tick(db, feed(RED), sent.append, now=at(3))
    (t,) = [x for x in trades(db) if x["symbol"] == "REDD"]
    assert t["status"] == "open" and lb._red_pending(RED.iloc[:3], t, pd.Timestamp(t["entry_ts"]))
    lb.tick(db, feed(RED), sent.append, now=at(4))                             # the next candle closed: out at its open
    assert [x for x in trades(db) if x["symbol"] == "REDD"][0]["reason"] == "red"


def test_the_other_not_bought_reasons_keep_their_place_and_their_counts(db, monkeypatch):
    monkeypatch.setattr(lb, "costly", lambda symbol, t: True)
    run(db, STOPPED)
    assert trades(db)[0]["status"] == lb.COSTLY            # the cost cap is checked first, as before: its test's sample
    monkeypatch.setattr(lb, "costly", lambda symbol, t: False)
    db2 = OpsStore(db.path.parent / "off.db")
    db2.set_flag(lb.OFF_FLAG, "1")
    sent = run(db2, STOPPED)
    assert trades(db2)[0]["status"] == lb.OVER and sent == []   # measure-only texts what the book WOULD open: not this


def test_the_nightly_settle_replays_the_recorded_order_on_official_candles_and_keeps_it_out_of_every_total(db):
    run(db, RED)
    run(db, DAY, sym="GOOD")
    out = lb.settle(db, sip({"ABCD": DAY, "GOOD": DAY}), cost, now=NIGHT)      # the official tape: this one ran
    assert out == ["ABCD L4b not opened (invalidated when seen): its order replayed on official candles +5.64% "
                   "(hypothetical; no position existed)", "GOOD L4b +5.64%"]
    over, good = trades(db)
    assert (over["status"], over["net"], over["calc"], over["calc_v1"]) == ("over", pytest.approx(0.0564, abs=1e-4), None, None)
    assert good["status"] == "settled" and over["settled_at"] is not None
    assert lb.settle(db, sip({"ABCD": DAY, "GOOD": DAY}), cost, now=NIGHT) == []           # scored once
    assert lb.nav(db) == pytest.approx(100_000 * (1 + good["weight"] * good["net"]))       # the opened long alone
    assert lb.today_line(db, "2026-11-09") == "longs (paper) 1 trades: GOOD red-candle break +5.6%"
    assert lb.over_line(db, "2026-11-09") == (
        "long setups recorded, not opened (1; no position, not in the total; a figure is the order's replay on official "
        "candles): expired 0; invalidated 1: ABCD red-candle break (fill candle closed under the entry) +5.6%; "
        "insufficient data 0")
    assert lb.over_check_line(db) == (
        "long entry check since 11-09 (long-live-entry-v2): 1 opened, 1 recorded only (0 expired, 1 invalidated, "
        "0 insufficient data); the 1 replayed on official candles average +5.6% (hypothetical). The recorded ones are "
        "in no total and do not join the registered sample")
    # the ledger and the registered sample read settled trades only: the recorded one is not among them
    with db.read() as c:
        assert [r[0] for r in c.execute("SELECT symbol FROM long_trades WHERE status='settled'")] == ["GOOD"]
    assert alert_feed.long_words("over", over["net"], over["not_opened"]) == (
        "long not opened (invalidated): its red-candle exit was already determined when the book saw the fill (out at "
        "the next candle's open): its order replayed on official candles +5.6%")
    assert alert_feed.long_words("over", over["net"]) == ("long not opened: already over when the book saw it: its order "
                                                          "replayed on official candles +5.6%")      # a caller without it
    assert alert_feed.long_words("over", None) == "long not opened: already over when the book saw it"


def test_rows_made_before_the_rule_are_not_touched(db):
    """Prospective only: a long opened and closed under the earlier behaviour (FCUV 11-25, booked 66 s after its fill
    candle began, its red exit already determined) stays the live paper long it was, and settles as one."""
    fid = alert_row(db, "FCUV")
    with db.tx() as c:
        c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, trigger, stop, target, weight, entry_ts, "
                  "est_entry, est_exit, est_exit_at, created_at, rules, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (fid, "FCUV", "2026-11-09", "L4b", "est_closed", 10.55, 10.2, 11.25, 0.002, (T1 + 2 * pt.BAR).isoformat(),
                   10.55, 10.42, (T1 + 3 * pt.BAR).isoformat(), at(3, 6).isoformat(), lb.RULES, "red"))
    for k in range(6, 12):
        lb.tick(db, feed(RED), [].append, now=at(0, 30 * k))
    assert lb.settle(db, sip({"FCUV": RED}), cost, now=NIGHT) == [f"FCUV L4b {10.42 / 10.55 - 1 - 0.01:+.2%}"]
    (t,) = trades(db)
    assert (t["status"], t["exit_reason"]) == ("settled", "red") and lb.over_check_line(db) is None


def test_a_long_bought_at_its_break_candles_close_is_never_over(db):
    """``already_over`` on a buy whose reference is the candle that has just closed: no later candle exists yet.
    The break candle is 15:03 (the last of ``DAY.iloc[:3]``); the setup's ready time and the buy's entry time are the
    start of the NEXT candle, 15:04, which the book does not hold yet."""
    s = pt.Setup("L8", "long", T1 + 3 * pt.BAR, None, 10.2)
    t = pt.Trade("L8", "long", s.ready, 10.65, s.ready, 10.65, "open", 0.0)
    assert lb.already_over(DAY.iloc[:3], {"session": "2026-11-09"}, s, t, at(3, 10)) is None
    # and once a LATER candle is held (15:04, after the buy's entry time) and it reached the stop: over like any long
    low = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.65, 500),
                (10.65, 10.7, 10.0, 10.1, 800)])
    assert lb.already_over(low, {"session": "2026-11-09"}, s, t, at(4, 10))["exit"] == "stop"


def test_a_break_candle_that_spans_its_own_stop_and_target_does_not_make_a_buy_at_its_close_over(tmp_path, monkeypatch):
    """Review 2026-11-25 asked whether a base break bought at its candle's close could be classed over by that same
    candle (a wick to the stop, a spike past the 2R target). It cannot: the buy's entry time is the START of the next
    candle (``Setup.ready`` = break candle + one bar), the buy is made only while no candle from then on is held
    (``long_armed.pending``), and the exit code reads candles from the entry time on. What happened inside the break
    candle happened before the buy. Run through the live pass on the frame the runner holds at that moment."""
    from sgt.fast import long_shadow

    from tests.fast import test_long_base as tb

    monkeypatch.setattr(lb, "LIVE_KINDS", ("L8", "L5v2"))                      # the live kinds, not this file's default
    monkeypatch.setattr(lb, "LIVE_WATCH", max(pt.WATCH, pt.BASE_WATCH))
    wide = (10.4, 12.0, 10.2, 10.6, 500)                 # low under the stop 10.25, high far above the target 11.30
    day = tb.bars([tb.SPIKE] + [tb.QUIET] * 11 + [wide, tb.FILL, tb.RUN] + [tb.AFTER] * 60)
    now = tb.at(13, 5)                                   # five seconds after the break candle closed
    f = lb.rth(day, now)
    row = {"id": 1, "symbol": "ABCD", "session": "2026-11-09", "posted_at": tb.POSTED.isoformat()}
    s, t = lb.at_close(f, row, now, long_shadow._latest("2026-11-09"))
    assert (f.ts.iloc[-1], f.low.iloc[-1], f.high.iloc[-1]) == (s.ready - pt.BAR, 10.2, 12.0)      # it IS in the frame
    assert s.stop == 10.25 and lb.target(s, t) == pytest.approx(11.3) and f[f.ts >= s.ready].empty
    assert lb.already_over(f, row, s, t, now) is None
    db = OpsStore(tmp_path / "base.db")
    tb.alert_row(db)
    sent = []
    assert lb.tick(db, tb.feed(day), sent.append, now=now) == 1 and bold("LONG IN") in sent[0]
    (r,) = tb.trades(db)
    assert (r["status"], r["kind"], r["est_entry"]) == ("open", "L8", 10.6)


# ---- owner 2026-11-26: by each setup's specification; a still-valid setup is opened; unknown is said, never booked ----
ROW = {"id": 1, "session": "2026-11-09"}
NAN = float("nan")


def test_the_class_names_on_the_site_are_the_books_own():
    assert alert_feed.NOT_OPENED == lb.CLASSES == ("expired", "invalidated", "insufficient data")
    assert lb.CLASS_OF == {"stop": "invalidated", "red": "invalidated", "target": "expired", "time": "expired"}


def test_each_setup_is_judged_by_its_own_specification():
    """The candle after the entry closes UNDER the entry, above the stop. A trigger setup's specification (rules A2)
    has the red-candle exit: invalidated. A base break's (rules B1) has none: still valid, opened."""
    day = bars([(10.0, 10.5, 10.0, 10.4, 900), (10.5, 10.55, 10.2, 10.3, 400), (10.3, 10.7, 10.3, 10.65, 500),
                (10.6, 10.62, 10.45, 10.5, 300)])
    entry_ts = T1 + 3 * pt.BAR                                                 # the 15:04 candle
    flag = pt.Setup("L5v2", "long", entry_ts, 10.6, 10.2)
    base = pt.Setup("L8", "long", entry_ts, None, 10.2)
    t = pt.Trade("L5v2", "long", entry_ts, 10.6, entry_ts, 10.6, "open", 0.0)
    assert (lb.rules_of(flag), lb.rules_of(base)) == (lb.RULES, lb.RULES_BASE)
    got = lb.already_over(day, ROW, flag, t, at(4, 10))
    assert (got["class"], got["exit"]) == ("invalidated", "red")
    assert lb.already_over(day, ROW, base, t, at(4, 10)) is None
    # the stop is in every specification: one more candle, down through 10.20, and both are invalidated
    low = pd.concat([day, bars([(10.5, 10.5, 10.1, 10.15, 700)], start=T1 + 4 * pt.BAR)], ignore_index=True)
    assert lb.already_over(low, ROW, base, t, at(5, 10))["class"] == "invalidated"


def test_a_fill_whose_candle_closed_minutes_ago_is_still_opened_while_its_exit_is_undetermined(db):
    """"Do not reject a still-valid setup merely because its qualifying candle completed earlier": first seen four and
    a half minutes after the fill candle began (inside the book's five-minute window), three closed candles later,
    none of them at the stop or the target and the fill candle not red. Opened, as before the rule."""
    alert_row(db)
    sent = []
    assert lb.tick(db, feed(CALM), sent.append, now=at(6, 30)) == 1
    (t,) = trades(db)
    assert (t["status"], t["est_entry"], t["not_opened"]) == ("open", 10.55, None) and bold("LONG IN") in sent[0]
    assert lb.over_line(db, "2026-11-09") is None and lb.over_check_line(db) is None


def test_a_fill_past_the_books_five_minute_window_is_expired_and_says_which_window(db):
    """The existing eligibility window (LIVE_LAG). The status stays ``missed``, as before; the row now carries the
    class and the cause, and the site says them. A row made before this version keeps its old words."""
    alert_row(db)
    sent = []
    assert lb.tick(db, feed(CALM), sent.append, now=at(7, 30)) == 0 and sent == []
    (t,) = trades(db)
    assert t["status"] == "missed" and json.loads(t["not_opened"]) == {
        "version": "long-live-entry-v2", "class": "expired", "cause": "fill older than 5 min when seen"}
    assert alert_feed.long_words("missed", None, t["not_opened"]) == "long not opened (expired): fill older than 5 min when seen"
    assert alert_feed.long_words("missed", None) == "long setup filled before the book was on"
    assert lb.over_line(db, "2026-11-09") == (
        "long setups recorded, not opened (1; no position, not in the total; a figure is the order's replay on official "
        "candles): expired 1: ABCD red-candle break (fill older than 5 min when seen); invalidated 0; insufficient data 0")
    assert lb.over_check_line(db) == (
        "long entry check since 11-09 (long-live-entry-v2): 0 opened, 1 recorded only (1 expired, 0 invalidated, "
        "0 insufficient data). The recorded ones are in no total and do not join the registered sample")
    assert lb.settle(db, sip({"ABCD": CALM}), cost, now=NIGHT) == []           # a missed fill is not replayed, as before


def test_an_unknown_case_is_recorded_with_its_reason_and_never_opened(db):
    """The candle after the fill candle has no usable low: whether the stop was reached cannot be told. Recorded as
    insufficient data with the reason in words; nothing is opened then or later, and nothing is texted."""
    day = CALM.copy()
    day.loc[3, "low"] = NAN                                                     # the 15:04 candle
    alert_row(db)
    sent = []
    for k in range(0, 60):
        lb.tick(db, feed(day), sent.append, now=at(4, 5 + 30 * k))
    (t,) = trades(db)
    words = ("it could not be checked when the book saw the fill: the free-feed candle of 10:04 ET has no usable price, "
             "so its stop and target cannot be checked")
    assert (t["status"], t["reason"], t["est_exit"]) == ("over", words, None) and sent == []
    why = json.loads(t["not_opened"])
    assert (why["class"], why["cause"]) == ("insufficient data", words) and "exit" not in why
    assert lb.over_line(db, "2026-11-09").endswith(f"expired 0; invalidated 0; insufficient data 1: ABCD red-candle break ({words})")
    assert alert_feed.long_words("over", None, t["not_opened"]) == f"long not opened (insufficient data): {words}"
    assert lb.nav(db) == 100_000.0


def test_what_the_candles_before_an_unusable_one_already_settle_is_not_unknown():
    stopped = STOPPED.copy()
    stopped.loc[3, "high"] = NAN                         # the stop is on the fill candle, before the unusable one
    s = pt.Setup("L4b", "long", T1 + 2 * pt.BAR, 10.55, 10.2)
    t = pt.Trade("L4b", "long", T1 + 2 * pt.BAR, 10.55, T1 + 2 * pt.BAR, 10.55, "open", 0.0)
    got = lb.already_over(stopped.iloc[:6], ROW, s, t, at(6, 5))
    assert (got["class"], got["exit"], got["exit_px"]) == ("invalidated", "stop", 10.2)
    red = RED.copy()
    red.loc[3, "open"] = NAN                             # the fill candle closed red; the next open is unusable
    assert (lambda g: (g["class"], g["exit"]))(lb.already_over(red.iloc[:6], ROW, s, t, at(6, 5))) == ("invalidated", "red")
    calm = CALM.copy()
    calm.loc[2, "close"] = NAN                           # the fill candle itself: nothing before it settles anything
    assert lb.already_over(calm.iloc[:6], ROW, s, t, at(6, 5))["class"] == "insufficient data"


@pytest.mark.parametrize("stop", [NAN, 0.0, -1.0, None, float("inf")])
def test_a_long_whose_own_levels_are_not_prices_is_unknown(stop):
    s = pt.Setup("L4b", "long", T1 + 2 * pt.BAR, 10.55, stop)
    t = pt.Trade("L4b", "long", T1 + 2 * pt.BAR, 10.55, T1 + 2 * pt.BAR, 10.55, "open", 0.0)
    got = lb.already_over(CALM.iloc[:4], ROW, s, t, at(4, 5))
    assert got["class"] == "insufficient data" and got["exit"] is None
    assert got["cause"] in ("the long's entry, stop or target is not a usable price",
                            "the exit check could not be computed (TypeError)")


def test_a_check_that_fails_is_recorded_as_unknown_and_the_pass_goes_on(db, monkeypatch):
    def boom(*a, **k):
        raise ZeroDivisionError("x")

    monkeypatch.setattr(lb, "_exit", boom)
    alert_row(db)
    sent = []
    assert lb.tick(db, feed(CALM), sent.append, now=at(3)) == 0 and sent == []
    (t,) = trades(db)
    why = json.loads(t["not_opened"])
    assert t["status"] == "over" and why["class"] == "insufficient data"
    assert why["cause"] == ("it could not be checked when the book saw the fill: the exit check could not be computed "
                            "(ZeroDivisionError)")
    monkeypatch.undo()
    assert lb.tick(db, feed(CALM), sent.append, now=at(3, 30)) == 0 and len(trades(db)) == 1     # one row per alert


def test_a_row_whose_class_cannot_be_read_falls_back_to_the_plain_words():
    for bad in (None, "", "{", "[]", json.dumps({"class": "made up", "cause": "x"}), 7):
        assert alert_feed.not_opened_words(bad) == "" and lb.not_opened(bad) == {}
    long = json.dumps({"class": "expired", "cause": "y" * 900})
    assert len(alert_feed.not_opened_words(long)) == alert_feed.MAX_WHY
