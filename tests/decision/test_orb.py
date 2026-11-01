"""ORB event study: point-in-time opening range, next-bar entry, stop/close exits, costs."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sgt.brokers.costs import CostModel
from sgt.core.types import Bar, Segment
from sgt.decision.features import CostModelAdapter
from sgt.decision.orb import ORBConfig, open_rel_vol, orb_trade

SD = date(2024, 3, 12)                      # EDT: 09:30 ET == 13:30 UTC, close 16:00 ET
ZERO = CostModelAdapter(CostModel(hs_under_1=0, hs_1_to_5=0, hs_5_to_20=0, hs_over_20=0))


def _bar(minute, o, h, l, c, v=1000.0, sd=SD):
    ts = datetime(sd.year, sd.month, sd.day, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=minute - 570)
    return Bar(symbol="AAA", ts=ts, open=o, high=h, low=l, close=c, volume=v, session_date=sd,
               minute_of_day=minute, segment=Segment.RTH)


def _day(path_after, *, green=True, v=1000.0):
    """5 opening bars 10.0 -> 10.4 (high 10.5, low 9.9), then (o,h,l,c) tuples."""
    o0, c4 = (10.0, 10.4) if green else (10.4, 10.0)
    bars = [_bar(570, o0, 10.5, 9.9, 10.2, v)] + [_bar(570 + i, 10.2, 10.3, 10.0, 10.2, v) for i in range(1, 4)]
    bars.append(_bar(574, 10.2, 10.4, 10.0, c4, v))
    m = 575
    for o, h, l, c in path_after:
        bars.append(_bar(m, o, h, l, c, v)); m += 1
    return bars


def test_breakout_enters_next_open_and_holds_to_close():
    path = [(10.4, 10.6, 10.4, 10.55), (10.6, 10.7, 10.5, 10.6)] + [(11.0, 11.1, 10.9, 11.0)] * 380
    t = orb_trade(_day(path), [], ORBConfig(), ZERO)
    assert t is not None and t.entry_price == 10.6 and t.stop == 9.9
    assert t.exit_reason == "close" and abs(t.net_return - (11.0 / 10.6 - 1)) < 1e-12


def test_stop_exits_at_stop_or_gap_open():
    path = [(10.4, 10.6, 10.4, 10.55), (10.6, 10.7, 10.5, 10.6), (10.0, 10.1, 9.8, 9.85)]
    t = orb_trade(_day(path), [], ORBConfig(), ZERO)
    assert t.exit_reason == "stop" and t.exit_price == 9.9
    gap = [(10.4, 10.6, 10.4, 10.55), (10.6, 10.7, 10.5, 10.6), (9.5, 9.6, 9.4, 9.5)]
    assert orb_trade(_day(gap), [], ORBConfig(), ZERO).exit_price == 9.5


def test_red_opening_range_or_no_breakout_means_no_trade():
    path = [(10.4, 10.6, 10.4, 10.55), (10.6, 10.7, 10.5, 10.6)] + [(10.6, 10.6, 10.6, 10.6)] * 10
    assert orb_trade(_day(path, green=False), [], ORBConfig(), ZERO) is None
    flat = [(10.3, 10.45, 10.2, 10.3)] * 50                    # never closes above 10.5
    assert orb_trade(_day(flat), [], ORBConfig(), ZERO) is None


def test_open_rel_vol_uses_prior_sessions_only_and_filters():
    day = _day([(10.4, 10.6, 10.4, 10.55), (10.6, 10.7, 10.5, 10.6)], v=5000.0)
    prior = [_day([], v=1000.0), _day([], v=1500.0)]
    assert abs(open_rel_vol(day, prior, 5) - 5000 / 1250) < 1e-12
    assert orb_trade(day, prior, ORBConfig(min_open_rel_vol=5.0), ZERO) is None
    assert orb_trade(day, prior, ORBConfig(min_open_rel_vol=3.0), ZERO) is not None


def test_costs_reduce_net():
    path = [(10.4, 10.6, 10.4, 10.55), (10.6, 10.7, 10.5, 10.6)] + [(11.0, 11.1, 10.9, 11.0)] * 380
    cost = CostModelAdapter(CostModel(hs_under_1=0.01, hs_1_to_5=0.01, hs_5_to_20=0.01, hs_over_20=0.01))
    t = orb_trade(_day(path), [], ORBConfig(), cost)
    assert abs(t.net_return - (11.0 * 0.99 / (10.6 * 1.01) - 1)) < 1e-12 and t.net_return < t.gross_return
