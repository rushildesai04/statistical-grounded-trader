"""Unit tests for the ORB-stocks-in-play simulation core (prereg_orb_stocks_in_play_2026-11-05).

Synthetic frames chosen so each test FAILS on a specific wrong implementation: look-ahead in
the daily filters / RVOL, wrong direction, missing gap-through on entry or stop, the entry-bar
worst-case stop, exiting at the wrong bar, double-counting costs, or non-deterministic ranking.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from sgt.research import orb_sip as orb

ET_OPEN = orb.OPEN_MIN


def _daily(sym, dates, vol, hi=None, lo=None, close=None, op=None):
    n = len(dates)
    hi = hi if hi is not None else [10.0] * n
    lo = lo if lo is not None else [9.0] * n
    close = close if close is not None else [9.5] * n
    op = op if op is not None else [9.2] * n
    return pd.DataFrame({"symbol": sym, "date": dates, "open": op, "high": hi, "low": lo,
                         "close": close, "volume": vol})


# --------------------------------------------------------------------------- daily filters / look-ahead
def test_adv_and_atr_are_prior_sessions_only_no_lookahead():
    dates = [date(2021, 1, d) for d in range(4, 25)]        # 21 sessions
    vol = [1_000_000] * 20 + [50_000_000]                   # a volume spike on the LAST day
    daily = _daily("AAA", dates, vol)
    f = orb.build_daily_filters(daily)
    last = f[f["date"] == dates[-1]].iloc[0]
    prior14 = np.mean(vol[-15:-1])                          # prior 14 sessions, excluding the spike day
    assert last["adv14"] == pytest.approx(prior14)
    assert last["adv14"] < 2_000_000                        # the same-day spike must NOT leak in
    # ATR as of the last day must not see the last day's range.
    hi = [10.0] * 20 + [100.0]
    lo = [9.0] * 20 + [1.0]
    daily2 = _daily("AAA", dates, vol, hi=hi, lo=lo, close=[9.5] * 21)
    f2 = orb.build_daily_filters(daily2)
    assert f2[f2["date"] == dates[-1]].iloc[0]["atr14"] == pytest.approx(1.0, abs=1e-9)


def test_wilder_atr_matches_manual_recursion():
    hi = [float(10 + i) for i in range(16)]
    lo = [float(9 + i) for i in range(16)]
    close = [float(9.5 + i) for i in range(16)]
    daily = pd.DataFrame({"high": hi, "low": lo, "close": close})
    atr = orb.wilder_atr14(daily)
    tr = [hi[0] - lo[0]]
    for i in range(1, 16):
        tr.append(max(hi[i] - lo[i], abs(hi[i] - close[i - 1]), abs(lo[i] - close[i - 1])))
    first = np.mean(tr[:14])
    assert atr.iloc[13] == pytest.approx(first)
    assert atr.iloc[14] == pytest.approx((first * 13 + tr[14]) / 14)
    assert np.isnan(atr.iloc[12])                          # no ATR before 14 TRs


def test_daily_filter_thresholds():
    dates = [date(2021, 1, 1) + pd.Timedelta(days=i) for i in range(20)]
    # price just below $5 fails even with huge volume/ATR.
    daily = _daily("LOW", dates, [5_000_000] * 20, op=[4.99] * 20)
    f = orb.build_daily_filters(daily)
    assert not f[f["date"] == dates[-1]].iloc[0]["passes"]


# --------------------------------------------------------------------------- opening range / RVOL
def _window(sym, d, vols, opens=None, highs=None, lows=None, closes=None):
    """Five 1-min bars 09:30..09:34 for one symbol/day."""
    mins = [ET_OPEN + i for i in range(len(vols))]
    opens = opens if opens is not None else [10.0] * len(vols)
    highs = highs if highs is not None else [10.1] * len(vols)
    lows = lows if lows is not None else [9.9] * len(vols)
    closes = closes if closes is not None else [10.0] * len(vols)
    return pd.DataFrame({"symbol": sym, "date": d, "minute": mins, "open": opens,
                         "high": highs, "low": lows, "close": closes, "volume": vols})


def test_rvol_counts_missing_as_zero_and_requires_ten_sessions_no_lookahead():
    sessions = [date(2021, 2, d) for d in range(1, 20)]    # 19 synthetic sessions
    # BBB trades on only 9 of the first 18 sessions -> fails the >=10 requirement on day 19.
    rows = []
    for i, d in enumerate(sessions[:-1]):
        if i % 2 == 0:                                      # 9 sessions with bars
            rows.append(_window("BBB", d, [1000] * 5))
    rows.append(_window("BBB", sessions[-1], [9999] * 5))   # the decision day has bars
    agg = orb.aggregate_opening(pd.concat(rows, ignore_index=True))
    rv = orb.opening_rvol(agg, sessions)
    last = rv[(rv["symbol"] == "BBB") & (rv["date"] == sessions[-1])]
    assert last["rvol"].isna().all()                        # only 9 prior sessions with bars

    # CCC trades every session: denom = mean of prior 14 (future sessions must not change it).
    rows2 = [_window("CCC", d, [1000] * 5) for d in sessions[:-1]]
    rows2.append(_window("CCC", sessions[-1], [5000] * 5))
    agg2 = orb.aggregate_opening(pd.concat(rows2, ignore_index=True))
    rv2 = orb.opening_rvol(agg2, sessions)
    val = rv2[(rv2["symbol"] == "CCC") & (rv2["date"] == sessions[-1])].iloc[0]
    assert val["rvol"] == pytest.approx(5000.0 / 1000.0)   # 5000 / mean(1000) = 5


def test_direction_from_0934_close_vs_0930_open():
    assert orb.direction(10.0, 10.5) == 1
    assert orb.direction(10.0, 9.5) == -1
    assert orb.direction(10.0, 10.0) == 0


def test_select_top_n_ranking_ties_deterministic():
    day = pd.DataFrame({
        "symbol": ["ZZZ", "AAA", "MMM", "BBB"], "passes": [True] * 4,
        "rvol": [3.0, 3.0, 5.0, 0.5], "or_open": [10.0] * 4})
    sel = orb.select_in_play(day, n=3)
    # BBB (rvol<1) excluded; MMM first; the 3.0 tie breaks by symbol ascending (AAA before ZZZ).
    assert sel["symbol"].tolist() == ["MMM", "AAA", "ZZZ"]
    assert sel["rank"].tolist() == [1, 2, 3]


# --------------------------------------------------------------------------- trade simulation
def _bars(prices, ts0="2021-03-01T14:35:00Z"):
    """Minute bars from (open, high, low, close) tuples with sequential UTC timestamps."""
    t0 = pd.Timestamp(ts0)
    ts = [t0 + pd.Timedelta(minutes=i) for i in range(len(prices))]
    o, h, low, c = zip(*prices)
    return pd.DataFrame({"ts": ts, "open": o, "high": h, "low": low, "close": c})


def test_entry_gap_through_fill_above_range_high():
    # Bar 0 opens at 11.0, already above the range high 10.5 -> long fills at the open, not 10.5.
    bars = _bars([(11.0, 11.2, 10.8, 11.1), (11.1, 11.3, 11.0, 11.2)])
    sim = orb.simulate(1, or_high=10.5, or_low=10.0, atr=5.0, bars=bars)
    assert sim["entry"] == pytest.approx(11.0)


def test_entry_fills_at_range_high_when_not_gapped():
    bars = _bars([(10.2, 10.6, 10.1, 10.5), (10.5, 10.7, 10.4, 10.6)])
    sim = orb.simulate(1, or_high=10.5, or_low=10.0, atr=5.0, bars=bars)
    assert sim["entry"] == pytest.approx(10.5)             # bar 0 high 10.6 >= 10.5, open below -> fill at 10.5


def test_stop_worst_case_inside_entry_bar():
    # Entry bar triggers (high 10.6) AND its low 10.0 crosses the stop (10.5 - 0.1*4 = 10.1).
    bars = _bars([(10.2, 10.6, 10.0, 10.3), (10.3, 10.4, 10.2, 10.35)])
    sim = orb.simulate(1, or_high=10.5, or_low=10.0, atr=4.0, bars=bars)
    assert sim["stopped"] and sim["exit_i"] == 0
    assert sim["exit"] == pytest.approx(10.1)              # stop = 10.5 - 0.4


def test_stop_gap_through_fills_at_later_bar_open():
    # Enter bar 0 at 10.5 (stop 10.5-0.4=10.1). Bar 1 opens at 9.0, below the stop -> fill at 9.0.
    bars = _bars([(10.4, 10.6, 10.3, 10.5), (9.0, 9.1, 8.9, 9.0)])
    sim = orb.simulate(1, or_high=10.5, or_low=10.0, atr=4.0, bars=bars)
    assert sim["stopped"] and sim["exit_i"] == 1
    assert sim["exit"] == pytest.approx(9.0)               # gapped through the stop at the open


def test_exit_at_last_bar_close_when_not_stopped():
    bars = _bars([(10.4, 10.6, 10.4, 10.5), (10.5, 10.9, 10.45, 10.8), (10.8, 11.0, 10.7, 10.95)])
    sim = orb.simulate(1, or_high=10.5, or_low=10.0, atr=5.0, bars=bars)
    assert not sim["stopped"] and sim["exit_i"] == 2
    assert sim["exit"] == pytest.approx(10.95)             # last RTH bar close


def test_short_mirror_entry_and_stop():
    # Short triggers when a bar low <= range low 10.0; stop = 10.0 + 0.1*4 = 10.4.
    bars = _bars([(10.1, 10.2, 9.9, 10.0), (10.0, 10.5, 9.95, 10.4)])
    sim = orb.simulate(-1, or_high=10.6, or_low=10.0, atr=4.0, bars=bars)
    assert sim["entry"] == pytest.approx(10.0)             # low 9.9 <= 10.0, open above -> fill at 10.0
    assert sim["stopped"] and sim["exit"] == pytest.approx(10.4)


# --------------------------------------------------------------------------- costs
class _Adapter:
    """Fixed round-trip spread so the cost math is checkable exactly."""
    def round_trip(self, symbol, ts, price):
        return 0.004, "test"


def test_cost_math_once_per_round_trip():
    entry = 20.0
    c = orb.cost_fractions("X", datetime(2021, 3, 1, 14, 35, tzinfo=timezone.utc), entry, _Adapter())
    comm_rt = 2 * orb.COMMISSION_PER_SHARE / entry          # commission both sides
    assert c["commission_only"] == pytest.approx(comm_rt)
    assert c["primary"] == pytest.approx(comm_rt + 0.004)   # commission + full spread, once
    assert c["half_spread"] == pytest.approx(comm_rt + 0.002)


def test_trade_record_nets_subtract_costs_once():
    bars = _bars([(10.5, 10.9, 10.45, 10.8), (10.8, 11.0, 10.7, 10.95)])
    rec = orb.trade_record("X", date(2021, 3, 1), 1, 1, or_high=10.5, or_low=10.0, or_open=10.2,
                           atr=5.0, bars=bars, adapter=_Adapter())
    gross = (10.95 - 10.5) / 10.5
    assert rec["gross"] == pytest.approx(gross)
    comm_rt = 2 * orb.COMMISSION_PER_SHARE / 10.5
    assert rec["net_primary"] == pytest.approx(gross - comm_rt - 0.004)
    assert rec["net_commission_only"] == pytest.approx(gross - comm_rt)


def test_no_entry_returns_none():
    bars = _bars([(10.0, 10.2, 9.9, 10.1), (10.1, 10.3, 10.0, 10.2)])  # never reaches 10.5
    assert orb.simulate(1, or_high=10.5, or_low=10.0, atr=5.0, bars=bars) is None


def test_one_trade_per_name_single_record():
    bars = _bars([(10.5, 10.9, 10.45, 10.8)] * 5)           # many breakout bars, still one trade
    rec = orb.trade_record("X", date(2021, 3, 1), 1, 1, 10.5, 10.0, 10.2, 5.0, bars, _Adapter())
    assert isinstance(rec, dict) and rec["entry"] == pytest.approx(10.5)


def test_stop_is_measured_from_the_executed_entry_not_the_trigger():
    # review 2026-11-05: long trigger 10.00, bar gaps open at 10.50; ATR 2 -> stop 0.20 below the FILL (10.30)
    bars = pd.DataFrame({"open": [10.50, 10.45], "high": [10.60, 10.50], "low": [10.35, 10.25],
                         "close": [10.40, 10.30]})
    s = orb.simulate(1, 10.0, 9.5, 2.0, bars)
    assert s["entry"] == 10.50 and s["stopped"] and s["exit_i"] == 1 and s["exit"] == pytest.approx(10.30)


def test_entry_bar_free_variant_only_skips_the_entry_bar_check():
    bars = pd.DataFrame({"open": [10.0, 10.2], "high": [10.3, 10.6], "low": [9.7, 10.1], "close": [10.2, 10.5]})
    worst = orb.simulate(1, 10.0, 9.5, 2.0, bars)
    free = orb.simulate(1, 10.0, 9.5, 2.0, bars, entry_bar_stop=False)
    assert worst["stopped"] and worst["exit_i"] == 0
    assert not free["stopped"] and free["exit"] == 10.5
