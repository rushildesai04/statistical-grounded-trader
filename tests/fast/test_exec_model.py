"""Execution model v1 (docs/findings/exec_model_2026-11-21.md): fills from timestamped quotes and trades."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sgt.fast import exec_model as em
from sgt.fast.est_costs import LOCATE

T0 = pd.Timestamp("2026-11-18 15:00:00", tz="UTC")              # 10:00 ET
CLOSE = pd.Timestamp("2026-11-18 21:00:00", tz="UTC")           # 16:00 ET


def s(sec: float) -> pd.Timestamp:
    return T0 + pd.Timedelta(seconds=sec)


def tape(trades, quotes, fail=False, close=CLOSE):
    """``trades``: (seconds after T0, price); ``quotes``: (seconds after T0, bid, ask[, bid_size, ask_size])."""
    tr = np.array(sorted((s(t).value, p, 100.0) for t, p in trades), dtype=float).reshape(-1, 3)
    qs = sorted(quotes)

    def quote(symbol, at):
        live = [q for q in qs if s(q[0]) <= at]
        if not live:
            return None
        q = live[-1]
        return {"bid": q[1], "ask": q[2], "bid_size": q[3] if len(q) > 3 else 1e9, "ask_size": q[4] if len(q) > 4 else 1e9}

    def trades_in(symbol, start, end):
        return None if fail else tr[(tr[:, 0] >= start.value) & (tr[:, 0] < end.value)]

    f = pd.DataFrame({"ts": [pd.Timestamp(int(t), tz="UTC").floor("min") for t in tr[:, 0]], "p": tr[:, 1]})
    bars = (f.groupby("ts").p.agg(open="first", high="max", low="min", close="last").reset_index()
            if len(f) else pd.DataFrame(columns=["ts", "open", "high", "low", "close"]))
    return em.Tape(quote, trades_in, bars, close)


def every(start, end, px, step=20):
    """A print every ``step`` seconds from ``start`` to ``end`` (seconds after T0) at ``px``."""
    return [(t, px) for t in range(start, end, step)]


# ---- fills ----
def test_a_sell_fills_at_the_bid_and_a_buy_at_the_ask_of_the_last_quote_before_the_order():
    t = tape(every(0, 120, 10.0), [(0, 9.95, 10.05), (30, 9.90, 10.10), (45, 9.80, 10.20)])
    assert em.fill(t, "A", "sell", s(40)).px == 9.90            # the 45 s quote is after the order: not known yet
    f = em.fill(t, "A", "buy", s(40))
    assert (f.px, f.how, f.at, f.flags) == (10.10, "quote", s(40), ())
    assert f.spread == pytest.approx(0.20 / 10.0)


def test_wide_and_thin_are_flagged_and_never_change_the_price():
    t = tape(every(0, 120, 10.0), [(0, 9.0, 11.0, 50, 50)])
    f = em.fill(t, "A", "sell", s(10), shares=400)
    assert f.px == 9.0 and set(f.flags) == {"wide", "thin"}
    assert em.fill(t, "A", "sell", s(10), shares=40).flags == ("wide",)


def test_no_trade_for_a_minute_after_the_order_waits_for_the_next_trade():
    # quotes stand, but nothing trades from 30 s to 300 s (a halt): the order fills at the reopening trade
    t = tape([(0, 10.0), (20, 10.0), (300, 12.0), (310, 12.1)], [(0, 9.95, 10.05)])
    f = em.fill(t, "A", "buy", s(30))
    assert (f.px, f.how, f.at, f.flags) == (12.0, "trade", s(300), ("halt",))


def test_an_unusable_quote_uses_the_next_trade():
    for q in ([], [(0, 0.0, 10.05)], [(0, 10.05, 9.95)]):      # none, a zero bid, a crossed quote
        f = em.fill(tape(every(0, 120, 10.0), q), "A", "sell", s(25))
        assert (f.px, f.how, f.at, f.flags) == (10.0, "trade", s(40), ("halt",))


def test_nothing_trades_again_is_the_last_price_and_unresolved():
    f = em.fill(tape([(0, 10.0), (20, 10.2)], [(0, 9.95, 10.05)]), "A", "sell", s(30))
    assert (f.px, f.how) == (10.2, "last") and "unresolved" in f.flags


def test_a_failed_request_is_no_data_never_a_number():
    t = tape(every(0, 120, 10.0), [(0, 9.95, 10.05)], fail=True)
    with pytest.raises(em.NoData):
        em.fill(t, "A", "sell", s(10))
    assert em.short(t, "A", s(10))["status"] == "no_data" and em.long(t, "A", s(10), 9.0)["status"] == "no_data"


# ---- the short ----
def test_short_sells_the_bid_one_second_after_the_decision_and_covers_the_ask_after_60_minutes():
    t = tape(every(0, 3700, 10.0), [(0, 9.95, 10.05), (1800, 9.45, 9.55)])
    r = em.short(t, "A", s(10))
    assert (r["status"], r["entry"], r["entry_at"], r["reason"]) == ("ok", 9.95, s(11), "60 min")
    assert r["exit"] == 9.55 and r["exit_at"] == s(11 + 3600 + 1)
    assert r["gross"] == pytest.approx((9.95 - 9.55) / 9.95) and r["net"] == pytest.approx(r["gross"] - LOCATE)
    assert r["stress_extreme_net"] is None and r["flags"] == ()


def test_short_stop_triggers_on_the_first_trade_through_and_fills_at_the_ask_after_it():
    # sold 10.00 (bid); stop 12.00. A trade at 12.05 at 600 s triggers; one second later the ask is 12.40
    trades = every(0, 3700, 10.0) + [(600, 12.05), (600.5, 12.6)]
    t = tape(trades, [(0, 10.0, 10.1), (600.2, 12.3, 12.4), (700, 9.9, 10.0)])
    r = em.short(t, "A", s(10))
    assert (r["reason"], r["exit"], r["exit_at"]) == ("stop", 12.4, s(601))
    assert r["net"] == pytest.approx((10.0 - 12.4) / 10.0 - LOCATE)
    assert r["stress_extreme_net"] == pytest.approx((10.0 - 12.6) / 10.0 - LOCATE)     # that minute's high


def test_short_stop_is_never_filled_better_than_its_level():
    trades = every(0, 3700, 10.0) + [(600, 12.05)]
    t = tape(trades, [(0, 10.0, 10.1), (600.5, 11.0, 11.1)])                        # the ask fell back under the level
    assert em.short(t, "A", s(10))["exit"] == pytest.approx(12.0)


def test_short_stop_into_a_halt_fills_at_the_reopening_trade():
    trades = every(0, 600, 10.0) + [(600, 12.05), (900, 15.0)] + every(920, 3700, 15.0)
    r = em.short(tape(trades, [(0, 10.0, 10.1)]), "A", s(10))
    assert (r["reason"], r["exit"], r["exit_how"]) == ("stop", 15.0, "trade") and "halt" in r["flags"]


def test_short_near_the_close_covers_a_minute_before_it():
    close = s(1800)
    r = em.short(tape(every(0, 1800, 10.0), [(0, 10.0, 10.1)], close=close), "A", s(10))
    assert r["reason"] == "close" and r["exit_at"] == close - pd.Timedelta(seconds=60) + em.LATENCY


def test_latency_is_the_only_thing_the_first_stress_scenario_changes():
    t = tape(every(0, 3700, 10.0), [(0, 9.95, 10.05), (12, 9.80, 9.90)])
    assert em.short(t, "A", s(10))["entry"] == 9.95
    assert em.short(t, "A", s(10), latency=em.STRESS_LATENCY)["entry"] == 9.80


# ---- the long ----
def test_long_buys_the_ask_and_the_target_fills_at_its_price_when_a_trade_reaches_it():
    # ask 10.00, stop 9.80 -> target 10.40. A trade at 10.45 at 300 s
    trades = every(0, 1900, 10.0) + [(300, 10.45)]
    r = em.long(tape(trades, [(0, 9.98, 10.0)]), "A", s(10), 9.8)
    assert (r["entry"], r["reason"], r["exit"], r["exit_at"]) == (10.0, "target", pytest.approx(10.4), s(300))
    assert r["net"] == pytest.approx(0.04) and r["stress_extreme_net"] is None


def test_long_stop_fills_at_the_bid_after_the_trigger_never_above_the_stop():
    trades = every(0, 1900, 10.0) + [(300, 9.78), (300.4, 9.5)]
    r = em.long(tape(trades, [(0, 9.98, 10.0), (300.3, 9.6, 9.7)]), "A", s(10), 9.8)
    assert (r["reason"], r["exit"], r["exit_at"]) == ("stop", 9.6, s(301))
    assert r["net"] == pytest.approx(9.6 / 10.0 - 1)
    assert r["stress_extreme_net"] == pytest.approx(9.5 / 10.0 - 1)                 # that minute's low
    held = em.long(tape(every(0, 1900, 10.0) + [(300, 9.78)], [(0, 9.98, 10.0), (300.5, 9.9, 9.95)]), "A", s(10), 9.8)
    assert held["exit"] == 9.8                                                       # the bid came back: still the stop


def test_stop_and_target_in_one_minute_follow_the_order_of_the_trades():
    up_first = every(0, 1900, 10.0) + [(300, 10.45), (320, 9.7)]
    down_first = every(0, 1900, 10.0) + [(300, 9.7), (320, 10.45)]
    q = [(0, 9.98, 10.0)]
    assert em.long(tape(up_first, q), "A", s(10), 9.8)["reason"] == "target"
    assert em.long(tape(down_first, q), "A", s(10), 9.8)["reason"] == "stop"


def test_long_time_exit_sells_the_bid_after_30_minutes():
    r = em.long(tape(every(0, 1900, 10.0), [(0, 9.98, 10.0), (1000, 10.08, 10.1)]), "A", s(10), 9.8)
    assert (r["reason"], r["exit"], r["exit_at"]) == ("time", 10.08, s(11 + 1800 + 1))


def test_an_ask_at_or_under_the_stop_is_no_trade():
    r = em.long(tape(every(0, 1900, 9.8), [(0, 9.78, 9.8)]), "A", s(10), 9.8)
    assert r["status"] == "no_trade"


def test_red_entry_minute_sells_at_the_next_minute_only_under_the_old_rules():
    trades = [(0, 10.0), (20, 10.0), (40, 9.95)] + every(60, 1900, 9.95)           # the entry minute ends under 10.00
    q = [(0, 9.98, 10.0), (50, 9.93, 9.96)]
    red = em.long(tape(trades, q), "A", s(10), 9.8, red_exit=True)
    assert (red["reason"], red["exit"], red["exit_at"]) == ("red", 9.93, s(61))
    assert em.long(tape(trades, q), "A", s(10), 9.8)["reason"] == "time"             # base break (rules B1): no red exit
    green = [(0, 10.0), (20, 10.0), (40, 10.05)] + every(60, 1900, 10.05)
    assert em.long(tape(green, q), "A", s(10), 9.8, red_exit=True)["reason"] == "time"
    stopped = [(0, 10.0), (20, 10.0), (40, 9.7)] + every(60, 1900, 9.7)             # stopped inside the entry minute
    assert em.long(tape(stopped, q), "A", s(10), 9.8, red_exit=True)["reason"] == "stop"


# ---- the short-sale price test ----
def test_ssr_today_carried_over_and_unknown():
    day = pd.DataFrame({"ts": [s(-600), s(-300), s(300)], "low": [9.5, 8.9, 5.0]})
    assert em.ssr(day, s(0), 10.0, None, None) is True               # 8.9 <= 9.0 before the entry
    assert em.ssr(day.iloc[:1], s(0), 10.0, None, None) is False
    assert em.ssr(day, s(-400), 10.0, None, None) is False           # the low came after this entry
    assert em.ssr(day.iloc[:1], s(0), 10.0, 8.0, 9.0) is True        # yesterday fell 10% from its prior close
    assert em.ssr(day, s(0), None, None, None) is None
