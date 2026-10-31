"""Rule construction for the pre-registered anomaly tests (scripts/anomalies_study.py)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import anomalies_study as an  # noqa: E402


def bars(day: date, prices: dict[int, tuple[float, float]]) -> pd.DataFrame:
    """prices: minute_of_day -> (open, close) for an RTH session."""
    return pd.DataFrame([{"session_date": day, "minute_of_day": m, "open": o, "close": c, "segment": "rth"}
                         for m, (o, c) in prices.items()])


def test_intraday_momentum_sign_returns_and_cost():
    d1, d2, d3 = date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)
    f = pd.concat([
        bars(d1, {599: (100, 100), 930: (100, 100), 959: (100, 100)}),
        bars(d2, {599: (101, 101), 930: (102, 102), 959: (102, 103.02)}),   # r1 > 0 -> long, +1%
        bars(d3, {599: (102, 102), 930: (100, 100), 959: (100, 99)}),       # r1 < 0 -> short, +1%
    ])
    r = an.intraday_momentum(f)
    assert list(r.index) == [d2, d3]                      # d1 has no previous close
    assert r.loc[d2, "r1"] == pytest.approx(0.01)
    assert r.loc[d2, "position"] == 1 and r.loc[d3, "position"] == -1
    assert r.loc[d2, "net"] == pytest.approx(0.01 - an.COST)
    assert r.loc[d3, "net"] == pytest.approx(0.01 - an.COST)


def test_intraday_momentum_skips_early_close_and_missing_bars():
    pre, ec, nxt = date(2026, 11, 25), date(2026, 11, 27), date(2026, 11, 30)   # 11-27 closes 13:00
    f = pd.concat([
        bars(pre, {599: (1, 1), 930: (1, 1), 959: (1, 1)}),
        bars(ec, {599: (1, 1), 930: (1, 1), 959: (1, 1)}),
        bars(nxt, {599: (1, 1), 959: (1, 1)}),                                    # no 15:30 bar
    ])
    assert an.intraday_momentum(f).empty


def test_overnight_close_to_next_open():
    d = pd.DataFrame({"open": [100, 101, 103], "close": [100.5, 102, 104]},
                     index=[date(2026, 3, 2), date(2026, 3, 3), date(2026, 3, 4)])
    r = an.overnight(d)
    assert list(r.index) == [date(2026, 3, 2), date(2026, 3, 3)]
    assert r.loc[date(2026, 3, 2), "ret"] == pytest.approx(101 / 100.5 - 1)
    assert r.loc[date(2026, 3, 3), "net"] == pytest.approx(103 / 102 - 1 - an.COST)


def test_turn_of_month_window_bounds():
    idx = pd.bdate_range("2026-01-26", "2026-02-10")      # Jan: 26..30, Feb: 2..10
    px = pd.Series(np.arange(len(idx), dtype=float) + 100, index=idx)
    tom, inside = an.turn_of_month(px)
    assert len(tom) == 1
    entry, exit_ = pd.Timestamp("2026-01-29"), pd.Timestamp("2026-02-04")   # 2nd-to-last Jan, 3rd Feb
    assert tom.index[0] == exit_
    assert tom.ret.iloc[0] == pytest.approx(px[exit_] / px[entry] - 1)
    assert list(inside[inside].index) == list(pd.to_datetime(["2026-01-30", "2026-02-02", "2026-02-03", "2026-02-04"]))
