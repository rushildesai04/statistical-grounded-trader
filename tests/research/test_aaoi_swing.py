"""Synthetic-bar tests for the AAOI swing study (prereg_aaoi_swing_2026-11-12).

Each test is built so that a wrong rule line fails it: the signal thresholds (C1 +4% & top third,
C2 -5%, C3 20-day Donchian), the next-open entry / correct-session exit, the 3-session non-overlapping
hold, the split guard, and the day-clustered bootstrap determinism are all checked on hand-made bars.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from aaoi_swing_study import (  # noqa: E402
    boot_lb, build_daily, c1_trades, c2_trades, c3_trades, hold_all,
)


def _min_rows(d: date, o: float, h: float, lo: float, c: float, full: bool = True) -> list[dict]:
    """Two RTH minute bars realising daily open=o, high=h, low=lo, close=c (requires lo<=o,c<=h).
    If ``full`` is False the 15:59 bar is omitted (session not full)."""
    mid = (o + c) / 2.0
    rows = [{"segment": "rth", "minute_of_day": 570, "session_date": d.isoformat(),
             "open": o, "high": h, "low": lo, "close": mid, "volume": 100.0}]
    if full:
        rows.append({"segment": "rth", "minute_of_day": 959, "session_date": d.isoformat(),
                     "open": mid, "high": c, "low": c, "close": c, "volume": 100.0})
    else:
        rows.append({"segment": "rth", "minute_of_day": 700, "session_date": d.isoformat(),
                     "open": mid, "high": c, "low": c, "close": c, "volume": 100.0})
    return rows


def _frame(days: list[dict]) -> pd.DataFrame:
    """days: list of {d, o, h, lo, c, full?}. Business-day calendar starting 2020-01-02."""
    base = date(2020, 1, 2)
    rows = []
    for i, spec in enumerate(days):
        dd = spec.get("d") or (base + timedelta(days=i))
        rows.append(spec | {"d": dd})
    out = []
    for spec in rows:
        out += _min_rows(spec["d"], spec["o"], spec["h"], spec["lo"], spec["c"], spec.get("full", True))
    return pd.DataFrame(out)


def test_build_daily_aggregates_minutes() -> None:
    d = build_daily(_frame([{"o": 10.0, "h": 12.0, "lo": 9.0, "c": 11.0}]))
    r = d.iloc[0]
    assert (r.open, r.high, r.low, r.close, bool(r.full)) == (10.0, 12.0, 9.0, 11.0, True)


def test_build_daily_marks_partial_session() -> None:
    d = build_daily(_frame([{"o": 10.0, "h": 12.0, "lo": 9.0, "c": 11.0, "full": False}]))
    assert not bool(d.iloc[0].full)


def test_c1_fires_on_strong_up_close_near_high_and_enters_next_open() -> None:
    # day0 baseline close 10; day1 close 10.6 = +6% cc, close at the high (top third); day2 is the trade.
    d = build_daily(_frame([
        {"o": 10.0, "h": 10.1, "lo": 9.9, "c": 10.0},    # day0
        {"o": 10.0, "h": 10.6, "lo": 10.0, "c": 10.6},   # day1 signal: cc=+6%, pos=1.0
        {"o": 10.0, "h": 11.0, "lo": 10.0, "c": 11.0},   # day2 entry open 10 -> close 11
    ]))
    t = c1_trades(d)
    assert len(t) == 1
    assert t.iloc[0].entry_date == d.iloc[2].date            # next-open entry, not same day
    assert t.iloc[0].gross == pytest.approx(11.0 / 10.0 - 1.0)


def test_c1_skips_up_day_that_closes_low_in_range() -> None:
    # +6% cc but close in the BOTTOM of the range -> pos < 2/3 -> no C1 trade.
    d = build_daily(_frame([
        {"o": 10.0, "h": 10.1, "lo": 9.9, "c": 10.0},
        {"o": 10.0, "h": 12.0, "lo": 10.0, "c": 10.6},   # cc +6%, pos = 0.3
        {"o": 10.0, "h": 11.0, "lo": 10.0, "c": 11.0},
    ]))
    assert len(c1_trades(d)) == 0


def test_c1_skips_up_day_below_threshold() -> None:
    d = build_daily(_frame([
        {"o": 10.0, "h": 10.1, "lo": 9.9, "c": 10.0},
        {"o": 10.0, "h": 10.3, "lo": 10.0, "c": 10.3},   # cc +3% < 4%
        {"o": 10.0, "h": 11.0, "lo": 10.0, "c": 11.0},
    ]))
    assert len(c1_trades(d)) == 0


def test_c2_fires_on_sharp_down_day() -> None:
    d = build_daily(_frame([
        {"o": 10.0, "h": 10.1, "lo": 9.9, "c": 10.0},
        {"o": 10.0, "h": 10.0, "lo": 9.3, "c": 9.4},     # cc -6% <= -5%
        {"o": 9.4, "h": 9.8, "lo": 9.3, "c": 9.6},       # entry open 9.4 -> close 9.6
    ]))
    t = c2_trades(d)
    assert len(t) == 1
    assert t.iloc[0].gross == pytest.approx(9.6 / 9.4 - 1.0)


def test_c2_skips_mild_down_day() -> None:
    d = build_daily(_frame([
        {"o": 10.0, "h": 10.1, "lo": 9.9, "c": 10.0},
        {"o": 10.0, "h": 10.0, "lo": 9.5, "c": 9.6},     # cc -4% > -5%
        {"o": 9.6, "h": 9.8, "lo": 9.5, "c": 9.6},
    ]))
    assert len(c2_trades(d)) == 0


def test_c3_breakout_three_day_hold_non_overlapping() -> None:
    # 21 flat sessions at 10 (close 10.0), then a breakout close 11 on index 21; prior-20 max = 10.
    days = [{"o": 10.0, "h": 10.05, "lo": 9.95, "c": 10.0} for _ in range(21)]
    days.append({"o": 10.0, "h": 11.0, "lo": 10.0, "c": 11.0})   # idx21 breakout signal
    days.append({"o": 11.0, "h": 11.5, "lo": 11.0, "c": 11.2})   # idx22 entry open
    days.append({"o": 11.2, "h": 11.6, "lo": 11.1, "c": 11.4})   # idx23
    days.append({"o": 11.4, "h": 12.0, "lo": 11.3, "c": 11.9})   # idx24 exit close
    days.append({"o": 12.0, "h": 13.0, "lo": 12.0, "c": 13.0})   # idx25 another breakout, but...
    d = build_daily(_frame(days))
    t = c3_trades(d)
    # first breakout -> entry idx22 open 11.0, exit idx24 close 11.9
    assert len(t) >= 1
    first = t.iloc[0]
    assert first.entry_date == d.iloc[22].date
    assert first.gross == pytest.approx(11.9 / 11.0 - 1.0)
    # idx25 breakout is INSIDE the first trade's hold window (idx22..24 -> next_allowed=25) so it is
    # allowed only from idx25; its entry would be idx26 which does not exist -> no second trade here.
    assert len(t) == 1


def test_c3_requires_breakout_above_prior_high() -> None:
    days = [{"o": 10.0, "h": 10.05, "lo": 9.95, "c": 10.0} for _ in range(21)]
    days.append({"o": 10.0, "h": 10.0, "lo": 9.9, "c": 9.95})    # no new high -> no breakout
    days += [{"o": 9.95, "h": 10.0, "lo": 9.9, "c": 9.95} for _ in range(3)]
    d = build_daily(_frame(days))
    assert len(c3_trades(d)) == 0


def test_split_guard_excludes_corporate_action_entry() -> None:
    # day1 down -6% (C2 signal); day2 open gaps 3x prev close -> ratio>2.5 -> entry session unclean -> skip.
    d = build_daily(_frame([
        {"o": 10.0, "h": 10.1, "lo": 9.9, "c": 10.0},
        {"o": 10.0, "h": 10.0, "lo": 9.3, "c": 9.4},
        {"o": 30.0, "h": 31.0, "lo": 29.0, "c": 30.0},   # ratio 30/9.4 ~ 3.2 -> excluded
    ]))
    assert len(c2_trades(d)) == 0


def test_hold_all_horizons() -> None:
    d = build_daily(_frame([
        {"o": 10.0, "h": 10.5, "lo": 9.5, "c": 10.2},
        {"o": 10.2, "h": 10.8, "lo": 10.0, "c": 10.6},
        {"o": 10.6, "h": 11.0, "lo": 10.4, "c": 10.9},
    ]))
    h1 = hold_all(d, 1)
    assert len(h1) == 3
    assert h1.iloc[0].gross == pytest.approx(10.2 / 10.0 - 1.0)   # open->close same session
    h3 = hold_all(d, 3)
    assert len(h3) == 1
    assert h3.iloc[0].gross == pytest.approx(10.9 / 10.0 - 1.0)   # open_0 -> close_2


def test_boot_lb_deterministic_and_cost_subtracted() -> None:
    gross = np.array([0.02, 0.01, 0.03, -0.01, 0.02, 0.00])
    dates = np.array([1, 2, 3, 4, 5, 6])
    lb_a = boot_lb(gross, dates, 0.0010, np.random.default_rng(20261112), 0.05 / 3)
    lb_b = boot_lb(gross, dates, 0.0010, np.random.default_rng(20261112), 0.05 / 3)
    assert lb_a == lb_b                                           # same seed -> identical
    # higher cost lowers the bound
    lb_hi = boot_lb(gross, dates, 0.0010, np.random.default_rng(20261112), 0.05 / 3)
    lb_lo = boot_lb(gross, dates, 0.0004, np.random.default_rng(20261112), 0.05 / 3)
    assert lb_lo > lb_hi
