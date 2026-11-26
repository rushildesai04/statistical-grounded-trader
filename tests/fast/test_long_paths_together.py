"""The long book's 2026-11-25 changes run TOGETHER, as the runner wires them (owner 2026-11-25 evening: "Verify what
main currently contains rather than assuming merge order preserved the intended behavior ... run focused integration
checks on the exact resulting commit").

Each change had its own tests and its own review; the defect found when they were merged (one turn list under one
name serving two rotations) was in none of them, because none ran the two together. These tests drive one long
through the live pass with every reader wired (the feed's last trade, one exchange's quote, the raw print for the
between-candle look), then through the nightly settle, the parallel arithmetic and the cohort reader, and check the
six things the owner named."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from sgt.fast import calc_v1, exec_book, fade, fade_rule, long_book as lb
from sgt.ops.sms_style import bold
from sgt.ops.store import OpsStore

from tests.fast.test_long_book import DAY, NIGHT, T1, alert_row, at, cost, feed, one_minute_setups, sip, trades  # noqa: F401

BOOKED = T1 + pd.Timedelta(minutes=3)                           # the 15:03 fill candle is seen when it has closed


@pytest.fixture()
def db(tmp_path):
    return OpsStore(tmp_path / "ops.db")


@pytest.fixture(autouse=True)
def fresh_turns(monkeypatch):
    monkeypatch.setattr(lb, "_looked", {})
    monkeypatch.setattr(lb, "_stop_looked", {})


class Wired:
    """The three readers ``scripts/fade_live.py`` hands to the long book, with what each was asked and when."""

    def __init__(self, db):
        self.db, self.calls, self.print = db, [], None       # ``print``: what the raw last-print reader returns

    def last_trade(self, symbol):
        self.calls.append(("last", symbol, len(trades(self.db))))
        self.last_trade.__func__.seen = {symbol: {"at": at(2, 5).isoformat(), "why": None}}
        return 10.61

    def venue_quote(self, symbol):
        self.calls.append(("quote", symbol, len(trades(self.db))))
        return {"bid": 10.40, "ask": 10.90, "bid_size": 100, "ask_size": 100, "at": at(2, 58).isoformat()}

    def stop_print(self, symbol):
        self.calls.append(("print", symbol, len(trades(self.db))))
        return self.print if self.print is not None else {"why": "no trade"}

    def tick(self, sent, now):
        return lb.tick(self.db, feed(DAY), sent.append, now=now, last_trade=self.last_trade,
                       venue_quote=self.venue_quote, stop_print=self.stop_print)


def decisions(db):
    with db.read() as c:
        return [dict(r) for r in c.execute("SELECT * FROM decisions WHERE book='long' ORDER BY id")]


def run_to_the_stop_print(db):
    """One long: booked at the registered trigger when its fill candle closed, then closed by a valid print."""
    alert_row(db)
    w, sent = Wired(db), []
    for k in range(0, 7):                                           # 15:01:00 .. 15:04:00
        w.tick(sent, at(0, 30 * k))
    return w, sent


def test_the_quote_estimate_sits_beside_the_registered_reference_and_moves_nothing(db):
    w, sent = run_to_the_stop_print(db)
    (t,) = trades(db)
    ref = json.loads(t["entry_ref"])
    # the registered reference: the buy-stop trigger of the fill candle, and everything derived from it
    assert (t["est_entry"], t["trigger"], t["stop"]) == (10.55, 10.55, 10.2)
    assert t["target"] == pytest.approx(10.55 + 2 * (10.55 - 10.2))            # from the booked price, not the quote
    assert ref["booked"] == 10.55 == t["est_entry"]
    # the estimate: its own fields, its own label, a different number, and nothing reads it back into the trade
    assert (ref["venue"], ref["venue_ask"], ref["venue_bid"]) == ("IEX (one exchange)", 10.90, 10.40)
    assert ref["venue_ask_vs_booked"] == round(10.90 / 10.55 - 1, 5) and ref["last_px"] == 10.61
    assert not {k for k in ("est_entry", "entry", "trigger", "stop", "target") if k in ref}
    assert f"{bold('BUY ABCD')} @ {bold('$10.55')}" in sent[0]
    assert "IEX offer $10.90" in sent[0] and "not a fill" in sent[0] and "A reference price, not a fill" in sent[0]
    # both quote readers were asked once, and only after the long was on the book
    assert [c for c in w.calls if c[0] != "print"] == [("last", "ABCD", 1), ("quote", "ABCD", 1)]


@pytest.mark.parametrize("bad", [
    {"px": 10.0, "at": (BOOKED - pd.Timedelta(seconds=1)).isoformat()},          # printed before the long was booked
    {"px": 10.0, "at": (BOOKED + pd.Timedelta(minutes=5)).isoformat()},          # "printed" after this look
    {"px": 10.0, "at": "2026-11-09T15:03:20"},                                   # a time with no zone
    {"px": 10.0, "at": "soon"}, {"px": 10.0}, {"px": "10.0", "at": BOOKED.isoformat()},
    {"px": float("nan"), "at": BOOKED.isoformat()}, {"px": -1.0, "at": BOOKED.isoformat()}, {"px": True, "at": BOOKED.isoformat()},
    {"px": 10.21, "at": (BOOKED + pd.Timedelta(seconds=10)).isoformat()},        # valid and timely, above the stop
    {"why": "request failed"}, None, "10.0", 10.0])
def test_the_between_candle_look_acts_only_on_a_valid_print_made_after_the_booking(db, bad):
    w, sent = run_to_the_stop_print(db)
    w.print = bad
    w.tick(sent, at(3, 30))                                                     # between the 15:03 and 15:04 closes
    (t,) = trades(db)
    assert (t["status"], t["stop_seen"], t["est_exit"]) == ("open", None, None) and len(sent) == 1


def test_no_entry_and_no_exit_is_dated_before_the_bot_could_have_acted_on_it(db):
    w, sent = run_to_the_stop_print(db)
    (t,) = trades(db)
    booked = pd.Timestamp(t["created_at"])
    assert booked == BOOKED and pd.Timestamp(t["entry_ts"]) == BOOKED - pd.Timedelta(minutes=1)
    # the entry is the registered candle reference, older than the booking: recorded as that, with its age, not hidden
    ref = json.loads(t["entry_ref"])
    assert (ref["age_s"], ref["age_from"]) == (60.0, "the start of the fill candle")
    assert "that candle began 60 s before the booking" in sent[0]
    # the look makes no entry and never closes at a print from before the booking, however far under the stop
    w.print = {"px": 9.0, "at": (BOOKED - pd.Timedelta(seconds=30)).isoformat()}
    w.tick(sent, at(3, 15))
    assert trades(db)[0]["status"] == "open" and len(trades(db)) == 1
    printed = BOOKED + pd.Timedelta(seconds=20)
    w.print = {"px": 10.15, "at": printed.isoformat()}
    w.tick(sent, at(3, 30))
    (t,) = trades(db)
    seen = json.loads(t["stop_seen"])
    assert pd.Timestamp(t["est_exit_at"]) == printed and booked <= printed <= pd.Timestamp(seen["looked_at"])
    assert (t["entry_ts"], t["est_entry"], t["created_at"]) == ((BOOKED - pd.Timedelta(minutes=1)).isoformat(), 10.55,
                                                                BOOKED.isoformat())      # the entry was not re-dated
    assert seen["version"] == lb.STOP_LOOK_VERSION and t["est_exit"] == 10.15


def test_one_close_one_exit_text_whatever_the_candles_and_the_prints_do_afterwards(db):
    w, sent = run_to_the_stop_print(db)
    w.print = {"px": 10.15, "at": (BOOKED + pd.Timedelta(seconds=20)).isoformat()}
    w.tick(sent, at(3, 30))
    first = dict(trades(db)[0])
    for k in range(8, 140):                 # 15:04 closes at the target, more prints under the stop keep arriving
        w.print = {"px": 10.0 - 0.01 * (k % 5), "at": at(0, 30 * k).isoformat()}
        w.tick(sent, at(0, 30 * k))
    (t,) = trades(db)
    assert {k: t[k] for k in ("status", "reason", "est_exit", "est_exit_at", "stop_seen")} == \
           {k: first[k] for k in ("status", "reason", "est_exit", "est_exit_at", "stop_seen")}
    assert (t["status"], t["reason"], t["est_exit"]) == ("est_closed", "stop", 10.15)
    assert [bold("LONG IN") in m for m in sent] == [True, False] and bold("LONG OUT") in sent[1] and len(sent) == 2
    closes = [d for d in decisions(db) if d["state_to"] == "est_closed"]
    assert len(closes) == 1 and closes[0]["state_from"] == "open" and lb.STOP_LOOK_VERSION in closes[0]["facts"]
    looks = sum(c[0] == "print" for c in w.calls)
    w.tick(sent, at(70))
    assert sum(c[0] == "print" for c in w.calls) == looks                # a closed long is never looked at again


def test_the_registered_replay_the_parallel_figure_and_the_cohort_never_read_the_live_additions(db, tmp_path):
    w, sent = run_to_the_stop_print(db)
    w.print = {"px": 10.15, "at": (BOOKED + pd.Timedelta(seconds=20)).isoformat()}
    w.tick(sent, at(3, 30))
    # the control: the same alert on the same candles with none of the 2026-11-25 readers wired
    plain = OpsStore(tmp_path / "plain.db")
    alert_row(plain)
    for k in range(0, 140):
        lb.tick(plain, feed(DAY), [].append, now=at(0, 30 * k))
    assert lb.settle(db, sip({"ABCD": DAY}), cost, now=NIGHT) == lb.settle(plain, sip({"ABCD": DAY}), cost, now=NIGHT) \
        == ["ABCD L4b +5.64%"]
    (t,), (p,) = trades(db), trades(plain)
    replay = ("status", "entry", "fill_ts", "exit", "exit_ts", "exit_reason", "gross", "cost_rt", "net", "calc",
              "exit_v1", "exit_reason_v1", "net_v1", "calc_v1")
    assert {k: t[k] for k in replay} == {k: p[k] for k in replay}          # official bars, the live order: identical
    assert (t["exit_reason"], t["calc"], t["calc_v1"]) == ("target", calc_v1.PRIMARY, calc_v1.SAME)
    # the live estimate differs (a stop seen on a print against a target on candles) and is kept as what it was
    assert (t["reason"], t["est_exit"], p["reason"]) == ("stop", 10.15, "target") and t["stop_seen"] and not p["stop_seen"]
    assert p["entry_ref"] and "venue" not in json.loads(p["entry_ref"])    # no quote reader: the reference alone
    # the two arithmetic versions keep their own columns: the registered figure is not the parallel one's column
    assert t["net"] == pytest.approx(t["net_v1"]) and calc_v1.line(db).startswith("arithmetic check since 11-25")
    # the cohort reader: a ledger row with the additive quote and one without give the same sample and the same review
    base = {"book": "long", "id": 1, "symbol": "ABCD", "session": "2026-11-25", "label": "MODELED",
            "model": {"net": -0.0091, "stress_extreme_net": -0.02}, "orig": {"net": -0.015}}
    with_quote = {**base, "entry_quote": {"feed": "sip", "bid": 6.5, "ask": 6.59, "ask_vs_booked": 0.01, "covers_order": False}}
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_text(json.dumps(base) + "\n")
    b.write_text(json.dumps(with_quote) + "\n")
    assert exec_book.summary(a, exec_book.REVIEW_FROM) == exec_book.summary(b, exec_book.REVIEW_FROM)
    assert exec_book.review("long", a) == exec_book.review("long", b) and exec_book.review("long", a)["n"] == 1
    assert (exec_book.REVIEW_FROM, exec_book.REVIEW_N, exec_book.EARLY, exec_book.EXTEND_N, exec_book.EXTEND_UNTIL) == \
        ("2026-11-23", {"short": 50, "long": 30}, {"short": (25, -0.05), "long": (15, -0.03)},
         {"short": 100, "long": 60}, "2027-02-26")


def test_the_short_card_still_shows_the_registered_stop_count_after_the_merge():
    assert (fade_rule.STOPS, fade_rule.BASE_N) == (194, 764) and fade_rule.STOP_RATE == 194 / 764
    assert fade.STOPS == 194                                              # the re-export main added is still there
    body = fade.ask_text("XYZ", "resumed", 4.2, 5000.0, {}, "", (0, None))
    assert "stopped 194 of 764 (25%)" in body and "16%" not in body
