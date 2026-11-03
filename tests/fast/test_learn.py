"""Fast large-cap study rows and books (src/sgt/fast/learn.py): labels, point-in-time slot feature, book math."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sgt.fast import learn
from sgt.intraday.calendar import default_calendar

ET = ZoneInfo("America/New_York")


def sessions(n: int, start=date(2023, 1, 3)) -> list[date]:
    cal, out, d = default_calendar(), [], start
    while len(out) < n:
        if cal.is_session(d) and cal.bounds(d).rth_close.astimezone(ET).hour == 16:
            out.append(d)
        d += timedelta(days=1)
    return out


def bars_for(days: list[date], seed: int) -> pd.DataFrame:
    rng, rows = np.random.default_rng(seed), []
    for i, d in enumerate(days):
        t0 = datetime(d.year, d.month, d.day, 9, 30, tzinfo=ET).astimezone(timezone.utc)
        px = 100.0 + i
        for m in range(390):
            o = px
            px = o * (1 + rng.normal(0, 0.001))
            rows.append({"ts": t0 + timedelta(minutes=m), "open": o, "high": max(o, px), "low": min(o, px),
                         "close": px, "volume": 1000.0})
    return pd.DataFrame(rows)


def daily_for(bars: pd.DataFrame) -> pd.DataFrame:
    b = bars.assign(d=pd.to_datetime(bars.ts, utc=True).dt.tz_convert(ET).dt.date)
    return b.groupby("d").agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"))


@pytest.fixture(scope="module")
def built():
    days = sessions(24)
    bars, spy = bars_for(days, 1), bars_for(days, 2)
    return days, bars, learn.build_rows(bars, daily_for(bars), spy, spy)


def test_labels_follow_the_exit_rule_on_each_grid(built):
    days, bars, rows = built
    b = bars[pd.to_datetime(bars.ts, utc=True).dt.tz_convert(ET).dt.date == days[5]].reset_index(drop=True)
    r = rows[rows.session == days[5]].set_index("k")
    assert r.loc[30, "y_30"] == pytest.approx(b.open[60] / b.open[30] - 1)
    assert r.loc[150, "y_120"] == pytest.approx(b.open[270] / b.open[150] - 1)
    assert r.loc[330, "y_60"] == pytest.approx(b.close[389] / b.open[330] - 1)      # k + H past the last bar
    assert r.loc[30, "y_close"] == pytest.approx(b.close[389] / b.open[30] - 1)
    assert np.isnan(r.loc[60, "y_60"]) and np.isnan(r.loc[60, "y_close"])           # off-grid minutes


def test_slot_ret_uses_only_previous_sessions(built):
    days, _, rows = built
    y = rows[rows.k == 30].set_index("session").y_120
    s = rows[rows.k == 30].set_index("session").slot_120
    assert s.iloc[: learn.SLOT_N].isna().all()
    assert s.iloc[learn.SLOT_N] == pytest.approx(y.iloc[: learn.SLOT_N].mean())
    assert s.iloc[learn.SLOT_N + 1] == pytest.approx(y.iloc[1: learn.SLOT_N + 1].mean())


def test_horizon_frame_keeps_its_grid_and_label(built):
    _, _, rows = built
    f = learn.horizon_frame(rows, "120")
    assert set(f.k) == {30, 150, 270} and {"label", "slot_ret"} <= set(f.columns)
    assert not any(c.startswith("y_") or (c.startswith("slot_") and c != "slot_ret") for c in f.columns)
    assert f.slot_ret.notna().any()


def test_name_book_fixed_capital_and_no_trade_days():
    days = pd.Index([date(2024, 1, 2), date(2024, 1, 3)], name="session")
    t = pd.DataFrame({"session": [days[0], days[0]], "label": [0.01, -0.02], "pred": [1.0, -1.0],
                      "lo_0.9": [-0.5, -0.5], "hi_0.9": [0.5, 0.5]})
    flat = t.assign(pred=0.0)
    got = learn.name_book({"A": t, "B": flat}, 0.9, 0.0004, n_names=4, days=days)
    assert got.loc[days[0]] == pytest.approx((0.01 - 0.0004 + 0.02 - 0.0004) / 4)
    assert got.loc[days[1]] == 0.0


def test_xs_book_longs_top_shorts_bottom_and_needs_eight_names():
    d0, d1 = date(2024, 1, 2), date(2024, 1, 3)
    syms = [f"S{i}" for i in range(8)]
    p = pd.DataFrame({"session": d0, "k": 30, "sym": syms, "pred": range(8), "label": [0.01 * i for i in range(8)]})
    thin = p.head(7).assign(session=d1)
    got = learn.xs_book(pd.concat([p, thin]), 0.0004, pd.Index([d0, d1]))
    assert got.loc[d0] == pytest.approx((-0.0 - 0.01 + 0.06 + 0.07) / 4 - 0.0004)
    assert got.loc[d1] == 0.0
