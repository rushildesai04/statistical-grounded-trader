"""Plumbing of prereg_alert_after_day_2026-11-17 on synthetic bars (the real map is made once)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import alert_after_day_study as st  # noqa: E402


def daily(closes, opens=None, highs=None, lows=None, start="2025-03-03"):
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=len(closes))]
    opens = opens or closes
    return pd.DataFrame({"open": opens, "high": highs or [max(o, c) for o, c in zip(opens, closes)],
                         "low": lows or [min(o, c) for o, c in zip(opens, closes)], "close": closes}, index=idx)


def test_after_measures_every_horizon_from_the_alert_days_close():
    d = daily([10.0, 12.0, 11.0, 9.0, 9.5, 9.0, 8.0, 8.0], opens=[10.0, 10.0, 13.0, 11.0, 9.0, 9.5, 9.0, 8.0],
              highs=[10.0, 12.5, 13.0, 11.0, 9.5, 9.5, 9.0, 8.0], lows=[10.0, 10.0, 11.0, 9.0, 9.0, 9.0, 8.0, 8.0])
    a = st.after(d, d.index[1])                                  # alert day: closed 12.0 in a 10.0-12.5 range
    assert a["overnight"] == pytest.approx(13.0 / 12.0 - 1) and a["next_session"] == pytest.approx(11.0 / 13.0 - 1)
    assert a["one_day"] == pytest.approx(11.0 / 12.0 - 1) and a["two_days"] == pytest.approx(9.0 / 12.0 - 1)
    assert a["five_days"] == pytest.approx(8.0 / 12.0 - 1) and a["close_pos"] == pytest.approx(0.8)
    assert st.after(d, d.index[3]) is None                       # five days ahead are not there
    assert st.after(d, "2024-01-02") is None


def test_parse_daily_refuses_bad_rows():
    ok = [{"t": "2025-03-03T05:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5}, {"t": "2025-03-04T05:00:00Z", "o": 1.5, "h": 2, "l": 1, "c": 1.2}]
    d = st.parse_daily(ok)
    assert list(d.index) == ["2025-03-03", "2025-03-04"] and d.close.tolist() == [1.5, 1.2]
    assert st.parse_daily([]).empty
    for bad in ({"c": 0}, {"h": 0.1}, {"o": float("nan")}):
        with pytest.raises(ValueError):
            st.parse_daily([{**ok[0], **bad}, ok[1]])
    with pytest.raises(ValueError):
        st.parse_daily([ok[0], ok[0]])                           # the same day twice
    with pytest.raises(KeyError):
        st.parse_daily([{"t": "2025-03-03T05:00:00Z"}])
    with pytest.raises(ValueError):
        st.parse_daily("no")


def frame(n=900, drift=0.0, seed=0):
    rng = np.random.default_rng(seed)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-02", periods=n // 3)]
    t = pd.DataFrame({"symbol": [f"S{i}" for i in range(n)], "session": [days[i % len(days)] for i in range(n)],
                      "cost": 0.009, "news": rng.random(n) < 0.4, "close_pos": rng.random(n)})
    for h in st.HORIZONS:
        t[h] = rng.normal(drift, 0.08, n)
    return t


def test_groups_and_the_long_finding_rule(monkeypatch):
    monkeypatch.setattr(st, "N_BOOT", 400)
    t = frame()
    assert st.members(t, "all").all() and (st.members(t, "news") ^ st.members(t, "no_news")).all()
    assert st.members(t, "strong_close").mean() == pytest.approx(1 / 3, abs=0.06)
    assert not (st.members(t, "strong_close") & st.members(t, "weak_close")).any()
    t["news"] = t.news.astype(object)
    t.loc[0, "news"] = None                                      # unknown news is in neither news group
    assert not st.members(t, "news")[0] and not st.members(t, "no_news")[0]
    flat = st.cells(frame(drift=0.0))
    assert len(flat) == 25 and not any(c["finding_long"] for c in flat)
    up = st.cells(frame(drift=0.04))                             # +4% a horizon: clears the 0.9% cost in the big cells
    assert all(c["finding_long"] for c in up if c["group"] == "all") and up[0]["long_net"] == pytest.approx(0.031, abs=0.01)
    barely = st.cells(frame(drift=0.009))                        # the move equals the cost: not a finding
    assert not any(c["finding_long"] for c in barely)
    small = st.cells(frame(n=240, drift=0.2))                    # under 300 events: never a finding
    assert not any(c["finding_long"] for c in small)
    down = st.cells(frame(drift=-0.06))
    assert all(c["short_side_note"] for c in down if c["group"] == "all") and not any(c["finding_long"] for c in down)


def test_returns_are_clipped_and_counted(monkeypatch):
    monkeypatch.setattr(st, "N_BOOT", 200)
    t = frame(n=600)
    t.loc[0, "one_day"] = 40.0                                   # an unadjusted reverse split
    (c,) = [x for x in st.cells(t) if x["group"] == "all" and x["horizon"] == "one_day"]
    assert c["clipped"] == 1 and c["mean"] < 0.02


def test_main_refuses_a_second_run(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "result_train.json").write_text("{}")
    called = []
    monkeypatch.setattr(st, "collect", lambda *a: called.append(1))
    assert st.main([]) == 2 and called == []
