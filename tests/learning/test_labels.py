"""Triple-barrier labeling tests (plan §9.1)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from sgt.core.types import Bar, Segment
from sgt.learning.labels import (
    BarrierOutcome,
    InsufficientBarsError,
    label_candidates,
    triple_barrier,
)

ENTRY_TS = datetime(2026, 10, 12, 14, 30, tzinfo=timezone.utc)


def _bar(minute: int, o: float, h: float, lo: float, c: float, v: float = 1000.0) -> Bar:
    ts = ENTRY_TS + timedelta(minutes=minute)
    return Bar(
        symbol="AAA", ts=ts, open=o, high=h, low=lo, close=c, volume=v,
        session_date=ts.date(), minute_of_day=ts.hour * 60 + ts.minute, segment=Segment.RTH,
    )


def test_take_profit_first():
    bars = [_bar(1, 100, 101, 99.5, 100.5), _bar(2, 100.5, 102.5, 100, 102)]
    out = triple_barrier(ENTRY_TS, 100.0, bars, take_profit_pct=2.0, stop_pct=2.0, max_bars=10)
    assert out.label == 1
    assert out.exit_price == pytest.approx(102.0)  # tp level 100*1.02
    assert out.bars_held == 2
    assert out.ret == pytest.approx(0.02)
    assert out.mfe >= 0.025


def test_stop_first():
    bars = [_bar(1, 100, 100.5, 97.5, 98.2)]
    out = triple_barrier(ENTRY_TS, 100.0, bars, take_profit_pct=2.0, stop_pct=2.0, max_bars=10)
    assert out.label == -1
    assert out.exit_price == pytest.approx(98.0)
    assert out.ret == pytest.approx(-0.02)


def test_both_touch_same_bar_stop_first_policy():
    bars = [_bar(1, 100, 103, 97, 100)]  # touches both 102 (tp) and 98 (stop)
    conservative = triple_barrier(ENTRY_TS, 100.0, bars, 2.0, 2.0, 10, same_bar_policy="stop_first")
    assert conservative.label == -1 and conservative.exit_price == pytest.approx(98.0)
    optimistic = triple_barrier(ENTRY_TS, 100.0, bars, 2.0, 2.0, 10, same_bar_policy="tp_first")
    assert optimistic.label == 1 and optimistic.exit_price == pytest.approx(102.0)


def test_gap_through_up_fills_at_open():
    bars = [_bar(1, 105, 106, 104.5, 105.5)]  # opens above the 102 tp
    out = triple_barrier(ENTRY_TS, 100.0, bars, 2.0, 2.0, 10)
    assert out.label == 1
    assert out.exit_price == pytest.approx(105.0)  # fill at open, not the tp level


def test_gap_through_down_fills_at_open():
    bars = [_bar(1, 95, 96, 94, 95.5)]  # opens below the 98 stop
    out = triple_barrier(ENTRY_TS, 100.0, bars, 2.0, 2.0, 10)
    assert out.label == -1
    assert out.exit_price == pytest.approx(95.0)


def test_timeout():
    bars = [_bar(i, 100, 100.3, 99.7, 100.1) for i in range(1, 6)]
    out = triple_barrier(ENTRY_TS, 100.0, bars, 2.0, 2.0, max_bars=3)
    assert out.label == 0
    assert out.bars_held == 3
    assert out.exit_price == pytest.approx(bars[2].close)


def test_no_future_bars_raises():
    with pytest.raises(InsufficientBarsError):
        triple_barrier(ENTRY_TS, 100.0, [], 2.0, 2.0, 10)


def test_insufficient_horizon_raises():
    bars = [_bar(1, 100, 100.3, 99.7, 100.1), _bar(2, 100.1, 100.4, 99.8, 100.2)]
    with pytest.raises(InsufficientBarsError):
        triple_barrier(ENTRY_TS, 100.0, bars, 2.0, 2.0, max_bars=5)


def test_bars_at_or_before_entry_ignored():
    pre = _bar(0, 100, 200, 100, 150)  # a huge bar AT entry_ts must be ignored
    nxt = _bar(1, 100, 100.3, 99.7, 100.1)
    bars = [pre, nxt]
    # Only one usable future bar, not enough for a timeout at max_bars=3 -> insufficient.
    with pytest.raises(InsufficientBarsError):
        triple_barrier(ENTRY_TS, 100.0, bars, 2.0, 2.0, max_bars=3)


def test_label_candidates_marks_missing_as_nan():
    bars_by_symbol = {
        "AAA": [_bar(1, 100, 102.5, 100, 102)],         # tp hit
        "BBB": [_bar(1, 100, 100.2, 99.8, 100.0)],      # too short -> NaN
    }
    cands = pd.DataFrame({
        "symbol": ["AAA", "BBB"],
        "ts": [ENTRY_TS, ENTRY_TS],
        "entry_price": [100.0, 100.0],
    })
    out = label_candidates(cands, lambda s: bars_by_symbol[s], 2.0, 2.0, max_bars=5)
    assert out.loc[0, "label"] == 1.0
    assert np.isnan(out.loc[1, "label"])
    assert int(out["label"].isna().sum()) == 1
    # original columns preserved
    assert list(cands.columns) == ["symbol", "ts", "entry_price"]


def test_outcome_is_frozen_dataclass():
    out = triple_barrier(ENTRY_TS, 100.0, [_bar(1, 100, 102.5, 100, 102)], 2.0, 2.0, 10)
    assert isinstance(out, BarrierOutcome)
    with pytest.raises(Exception):
        out.label = 0  # type: ignore[misc]
