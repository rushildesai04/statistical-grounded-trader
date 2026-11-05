from __future__ import annotations

import pandas as pd
import pytest

from sgt.fast import exits
from sgt.fast.exits import CHAMPION, Exit, indicators, simulate

T0 = pd.Timestamp("2026-11-05T14:30:00Z")


def frame(closes, highs=None, lows=None, opens=None, vol=1000.0):
    n = len(closes)
    opens = opens or closes
    return indicators(pd.DataFrame({
        "ts": [T0 + pd.Timedelta(minutes=i) for i in range(n)], "open": opens, "close": closes,
        "high": highs or [max(o, c) for o, c in zip(opens, closes)],
        "low": lows or [min(o, c) for o, c in zip(opens, closes)], "volume": [vol] * n}))


def test_champion_is_stop_then_last_close_of_60_minutes():
    b = frame([10.0] * 70)
    s = simulate(b, T0, 10.0, CHAMPION)
    assert s == {"gross": 0.0, "how": "cap", "minutes": 60}
    b = frame([10.0] * 5 + [11.0] * 65, highs=[10.0] * 5 + [12.5] * 65)
    s = simulate(b, T0, 10.0, CHAMPION)
    assert s["how"] == "stop" and s["gross"] == pytest.approx(-0.20) and s["minutes"] == 6


def test_stop_gap_covers_at_the_open_and_beats_any_signal_in_that_bar():
    b = frame([10.0, 13.0], opens=[10.0, 13.0], lows=[8.0, 8.0])
    s = simulate(b, T0, 10.0, Exit("t", "target", 60))
    assert s["how"] == "signal" and s["gross"] == pytest.approx(0.15)    # bar 0: target before any stop
    s = simulate(b, T0, 10.0, CHAMPION)
    assert s["how"] == "stop" and s["gross"] == pytest.approx(-0.30)     # gap over the stop fills at the open


def test_trail_arms_after_5pct_and_covers_next_open_after_a_5pct_bounce():
    closes = [10.0, 9.4, 9.0, 9.2, 9.5, 9.6, 9.6]          # low 9.0; 9.45 = 5% bounce; fires at 9.5 (bar 4)
    s = simulate(frame(closes), T0, 10.0, Exit("tr", "trail", 120))
    assert s["how"] == "signal" and s["minutes"] == 6 and s["gross"] == pytest.approx((10 - 9.6) / 10)


def test_trail_never_fires_before_it_is_armed():
    s = simulate(frame([10.0, 9.7, 10.3, 10.0]), T0, 10.0, Exit("tr", "trail", 120))
    assert s["how"] == "cap"


def test_vwap_needs_a_close_below_then_above_and_acts_next_bar():
    closes = [10.0, 9.0, 8.0, 8.0, 11.5, 11.0, 11.0]
    b = frame(closes)
    s = simulate(b, T0, 10.0, Exit("v", "vwap", 120))
    i = next(k for k in range(1, len(b)) if b.close[k] > b.vwap[k] and (b.close[:k] < b.vwap[:k]).any())
    assert s["how"] == "signal" and s["minutes"] == i + 2 and s["gross"] == pytest.approx((10 - b.open[i + 1]) / 10)


def test_ema_cross_up_uses_closes_only_and_last_bar_signal_covers_at_close():
    closes = [10.0] * 30 + [9.0] * 20 + [11.5] * 3
    b = frame(closes)
    s = simulate(b, T0, 10.0, Exit("e", "ema", 120))
    k = next(k for k in range(1, len(b)) if b.ema9[k] > b.ema21[k] and b.ema9[k - 1] <= b.ema21[k - 1])
    assert k == 50 and s["how"] == "signal" and s["gross"] == pytest.approx(-0.15)   # covers at the next open, 11.5
    s = simulate(frame(closes[:51]), T0, 10.0, Exit("e", "ema", 120))
    assert s["minutes"] == 51 and s["gross"] == pytest.approx(-0.15)                    # no next bar: that close


def test_no_bars_after_entry_is_no_trade():
    assert simulate(frame([10.0] * 3), T0 + pd.Timedelta(hours=1), 10.0, CHAMPION) is None


def test_challenger_set_is_the_preregistered_one():
    assert [(x.id, x.signal, x.cap_min, x.stop) for x in exits.EXITS] == [
        ("X-TRAIL", "trail", 120, 0.2), ("X-VWAP", "vwap", 120, 0.2), ("X-EMA", "ema", 120, 0.2),
        ("X-TGT", "target", 60, 0.2)]
