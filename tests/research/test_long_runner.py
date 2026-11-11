"""Runner exit logic for prereg_long_runner_2026-11-11 (synthetic bars; run before the single study run)."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_runner_study as lr  # noqa: E402

from sgt.fast import patterns as pt  # noqa: E402

T0 = pd.Timestamp("2026-03-02 15:00", tz="UTC")


def bars(rows):
    return pd.DataFrame({"ts": [T0 + i * pt.BAR for i in range(len(rows))], "open": [r[0] for r in rows],
                         "high": [r[1] for r in rows], "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                         "volume": 1000.0})


S = pt.Setup("L4b", "long", T0, 10.0, 9.5)        # buy-stop 10.00, stop 9.50, 2R target 11.00


RISE = [(9.9, 10.1, 9.8, 10.0),                  # 0 fill bar: buy-stop 10.00 fills at 10.00
        (10.2, 10.4, 10.15, 10.3), (10.3, 10.5, 10.2, 10.4),
        (10.4, 10.6, 10.1, 10.2),                  # 3 swing low 10.10 (confirmed when bar 5 closes)
        (10.3, 10.8, 10.3, 10.7)]


def test_runner_trails_the_latest_confirmed_swing_low_and_never_lowers_it():
    f = bars(RISE + [(11.0, 11.2, 10.95, 11.1),    # 5 target bar (11.00); trail still the stop
                     (11.1, 11.6, 11.0, 11.5),     # 6 trail -> 10.10 (swing low 3)
                     (11.5, 11.8, 10.9, 11.0),     # 7 swing low 10.90 (confirmed when bar 9 closes)
                     (11.2, 11.9, 11.2, 11.8), (11.8, 12.0, 11.4, 11.9),
                     (11.9, 11.9, 10.8, 10.9)])    # 10 trail 10.90 traded -> sells at min(10.90, open 11.90)
    t = pt.simulate(f, S)
    assert (t.reason, t.exit_ts) == ("target", T0 + 5 * pt.BAR) and t.exit == pytest.approx(11.0)
    px, at, why = lr.runner(f, S, t.entry_ts, t.exit_ts)
    assert (why, at) == ("trail", T0 + 10 * pt.BAR) and px == pytest.approx(10.9)
    assert lr.trail_at(8, 0, lr.swing_lows(f.low.to_numpy(float)), f.low.to_numpy(float), 9.5) == pytest.approx(10.1)


def test_runner_on_the_target_bar_is_conservative_and_rides_to_the_close():
    f = bars(RISE + [(10.7, 10.9, 10.5, 10.8),     # 5
                     (10.8, 11.2, 10.05, 11.1)])   # 6 target bar also trades below the 10.10 trail
    t = pt.simulate(f, S)
    assert t.reason == "target" and t.exit_ts == T0 + 6 * pt.BAR
    px, at, why = lr.runner(f, S, t.entry_ts, t.exit_ts)
    assert (why, at) == ("trail", T0 + 6 * pt.BAR) and px == pytest.approx(10.1)   # sold at the trail, same bar
    up = bars([(9.9, 10.1, 9.8, 10.0), (10.0, 10.6, 9.9, 10.5), (10.5, 11.2, 10.4, 11.1), (11.1, 11.5, 11.0, 11.4),
               (11.4, 11.8, 11.3, 11.7)])                                            # straight up: no swing low
    t = pt.simulate(up, S)
    px, at, why = lr.runner(up, S, t.entry_ts, t.exit_ts)
    assert (why, at) == ("close", T0 + 4 * pt.BAR) and px == pytest.approx(11.7)
    px60, _, why60 = lr.runner(up, S, t.entry_ts, t.exit_ts, cap=pd.Timedelta(minutes=3))
    assert why60 == "time" and px60 == pytest.approx(11.1)                          # last bar within 3 min of entry


def test_trade_rows_split_r1_into_half_target_half_runner_and_leave_non_targets_alone(monkeypatch):
    f = bars([(9.9, 10.1, 9.8, 10.0), (10.0, 10.6, 9.9, 10.5), (10.5, 11.2, 10.4, 11.1), (11.1, 11.5, 11.0, 11.4),
              (11.4, 11.8, 11.3, 11.7)])
    monkeypatch.setattr(lr, "first_fill", lambda f, t0, halt, cut: (S, pt.simulate(f, S)))
    ev = pd.DataFrame([{"symbol": "X", "session": "2026-03-02", "t0": T0, "cost_rt": 0.01}])
    row = lr.trade_rows(ev, lambda sym, ses: f, False)[0]
    assert row["base"] == pytest.approx(0.10 - 0.01) and row["r_all"] == pytest.approx(0.17 - 0.01)
    assert row["r1"] == pytest.approx(0.5 * 0.10 + 0.5 * 0.17 - 0.01)
    flat = bars([(9.9, 10.1, 9.8, 10.0)] + [(10.0, 10.2, 9.9, 10.1)] * 40)
    row = lr.trade_rows(ev, lambda sym, ses: flat, False)[0]
    assert row["base_reason"] == "time" and row["r1"] == row["base"] == row["r_all"] == row["r1_60"]


def test_first_fill_matches_the_live_book():
    """The study's trade per event is the live book's (long_book.first_fill) on the same bars and start bar."""
    from sgt.fast import long_book, long_shadow
    f = bars([(10.0, 10.2, 9.9, 10.1), (10.1, 10.3, 10.0, 10.2), (10.2, 10.25, 9.95, 10.0),   # red candle
              (10.0, 10.4, 9.98, 10.3)] + [(10.3, 10.5, 10.2, 10.4)] * 30)
    row = {"symbol": "X", "session": "2026-03-02", "posted_at": (T0 - pd.Timedelta(seconds=30)).isoformat(),
           "status": "settled"}
    found, t0 = long_shadow.setups(f, row)
    live = long_book.first_fill(f, row, long_shadow._latest("2026-03-02"))
    mine = lr.first_fill(f, t0, False, long_shadow._latest("2026-03-02"))
    assert live is not None and mine is not None
    assert live[0].kind == mine[0].kind == "L4b" and live[1] == mine[1]
