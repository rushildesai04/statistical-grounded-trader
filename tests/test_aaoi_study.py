"""Trade-rule mechanics for the AAOI intraday study (scripts/aaoi_study.py)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import aaoi_study as a  # noqa: E402

DAY = date(2026, 3, 2)


def minutes(rows: list[tuple[int, float, float, float, float]], vol: float = 1000) -> pd.DataFrame:
    """rows: (minute_of_day, open, high, low, close)."""
    return pd.DataFrame([{"session_date": DAY, "minute_of_day": m, "open": o, "high": h, "low": lo, "close": c,
                          "volume": vol} for m, o, h, lo, c in rows])


def day_row(**kw) -> pd.DataFrame:
    base = {"full": True, "clean": True, "atr": 2.0, "open": 50.0, "avg_vol": 2e6, "rv": 1.5,
            "or_open": 50.0, "or_close": 50.5, "or_high": 51.0, "or_low": 49.8}
    base.update(kw)
    return pd.DataFrame([base], index=[DAY])


def test_orb_long_trigger_and_hold_to_close():
    f = minutes([(570, 50, 51, 49.8, 50.5), (575, 50.9, 51.2, 50.85, 51.1), (959, 51.5, 52, 51.4, 52.0)])
    t = a.orb_trades(f, day_row())
    assert len(t) == 1 and t.side.iloc[0] == 1
    assert t.entry.iloc[0] == 51.0 and t.exit.iloc[0] == 52.0 and not t.stopped.iloc[0]
    assert t.net.iloc[0] == pytest.approx(1 / 51 - a.side_cost(51) - a.side_cost(52))
    assert t.R.iloc[0] == pytest.approx(t.net.iloc[0] / (0.2 / 51))       # risk = 10% of ATR 2.0


def test_orb_entry_and_stop_in_same_bar_is_stopped():
    f = minutes([(570, 50, 51, 49.8, 50.5), (575, 50.6, 51.2, 50.7, 50.9), (959, 52, 52, 52, 52)])
    t = a.orb_trades(f, day_row())
    assert t.stopped.iloc[0] and t.exit.iloc[0] == pytest.approx(50.8)


def test_orb_short_gap_through_stop_pays_open():
    rows = [(570, 50, 50.2, 49, 49.5), (575, 49.1, 49.15, 48.9, 49.0), (576, 49.6, 49.7, 49.5, 49.6), (959, 49, 49, 49, 49)]
    t = a.orb_trades(minutes(rows), day_row(or_close=49.5, or_low=49.0, or_high=50.2))
    assert t.side.iloc[0] == -1 and t.entry.iloc[0] == 49.0
    assert t.exit.iloc[0] == 49.6                                             # stop 49.2 gapped: pay the open


def test_orb_filters_and_doji():
    f = minutes([(570, 50, 51, 49.8, 50.5), (575, 50.6, 51.2, 50.6, 51.1), (959, 52, 52, 52, 52)])
    assert a.orb_trades(f, day_row(open=4.9)).empty
    assert a.orb_trades(f, day_row(rv=0.9)).empty
    assert a.orb_trades(f, day_row(rv=0.9), rv_min=0.0).shape[0] == 1
    assert a.orb_trades(f, day_row(or_close=50.0)).empty                      # doji


def test_vwap_long_all_day_and_costs():
    rows = [(m, 10 + 0.01 * k, 10 + 0.01 * k, 10 + 0.01 * k, 10 + 0.01 * k + 0.005)
            for k, m in enumerate(range(570, 960))]
    v = a.vwap_trades(minutes(rows))
    assert v.turns.iloc[0] == 2                                               # enter once, flatten once
    assert v.gross.iloc[0] > 0
    assert v.net.iloc[0] == pytest.approx(v.gross.iloc[0] - 2 * a.side_cost(float(np.mean([r[4] for r in rows]))))


def test_vwap_check_interval_limits_flips():
    rows = [(m, 10, 10.1, 9.9, 10 + (0.05 if k % 2 else -0.05)) for k, m in enumerate(range(570, 960))]
    assert a.vwap_trades(minutes(rows), every=30).turns.iloc[0] < a.vwap_trades(minutes(rows), every=1).turns.iloc[0]


def test_verdict_needs_lower_bound_halves_and_n():
    days = pd.bdate_range("2024-01-02", periods=150).date
    t = pd.DataFrame({"day": days, "sym": "AAOI", "net": 0.002, "R": 0.1, "gross": 0.003})
    assert a.verdict(t, np.random.default_rng(0))["pass"]
    assert not a.verdict(t.iloc[:90], np.random.default_rng(0))["pass"]                       # n < 100
    t.loc[:74, "net"] = -0.001
    assert not a.verdict(t, np.random.default_rng(0))["pass"]                                 # first half < 0
