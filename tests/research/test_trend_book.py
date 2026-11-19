"""Plumbing of prereg_trend_book_2026-11-18 on synthetic candles (the study itself runs once on training)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import trend_book_study as st  # noqa: E402

T0 = pd.Timestamp("2026-01-05 14:30", tz="UTC")
B = lambda *x: np.array(x, bool)  # noqa: E731


def test_in_when_all_green_out_when_macd_goes_red_and_in_again():
    o = np.arange(10, 20, dtype=float); c = o + 0.5
    enter = B(0, 1, 1, 1, 0, 0, 1, 1, 0, 0)
    macd = B(1, 1, 1, 1, 0, 0, 1, 1, 1, 1)
    t = st.round_trips(o, c, enter, macd, 0, 99)
    assert [(x["in_i"], x["out_i"]) for x in t] == [(2, 5), (7, 9)]          # second leg held to the last close
    assert t[0]["entry"] == o[2] and t[0]["exit"] == o[5] and t[1]["exit"] == c[9]


def test_no_entry_before_the_start_or_from_the_last_entry_minute_on():
    o = np.arange(10, 20, dtype=float); c = o + 0.5
    always, green = np.ones(10, bool), np.ones(10, bool)
    assert st.round_trips(o, c, always, green, 4, 99)[0]["in_i"] == 5
    assert st.round_trips(o, c, always, green, 0, 3)[0]["in_i"] == 1
    assert st.round_trips(o, c, always, green, 5, 6) == []                    # the first possible entry is already too late
    assert st.round_trips(o, c, np.zeros(10, bool), green, 0, 99) == []


def test_macd_red_at_the_entry_candles_close_sells_the_next_open():
    o = np.arange(10, 16, dtype=float); c = o + 0.5
    t = st.round_trips(o, c, B(1, 0, 0, 0, 0, 0), B(1, 0, 1, 1, 1, 1), 0, 99)
    assert [(x["in_i"], x["out_i"]) for x in t] == [(1, 2)]


def test_two_minute_candles_are_only_used_once_complete():
    ts = [T0 + pd.Timedelta(minutes=i) for i in range(6)]
    day = pd.DataFrame({"ts": ts, "open": [1, 2, 3, 4, 5, 6.0], "high": [2, 3, 4, 5, 6, 7.0], "low": [0.5, 1, 2, 3, 4, 5.0],
                        "close": [1.5, 2.5, 3.5, 4.5, 5.5, 6.5], "volume": [10, 20, 30, 40, 50, 60.0]})
    two = st.two_minute(day)
    assert list(two.open) == [1, 3, 5] and list(two.close) == [2.5, 4.5, 6.5] and list(two.high) == [3, 5, 7] and list(two.volume) == [30, 70, 110]
    assert two.done.iloc[0] == (T0 + pd.Timedelta(minutes=2)).tz_convert("UTC").tz_localize(None)
    up = pd.DataFrame({"ts": [T0 + pd.Timedelta(minutes=i) for i in range(200)], "close": np.linspace(10, 14, 200)})
    up["open"] = up.close.shift(1).fillna(10); up["high"] = up.close + 0.01; up["low"] = up.open - 0.01; up["volume"] = 1000.0
    all1, macd, all2 = st.columns(up, up.ts.to_numpy())
    assert all1[-1] and macd[-1] and all2[-1] and not all2[0]                 # nothing complete at the first minute


def table(n=1500, edge=0.0, seed=4):
    rng = np.random.default_rng(seed)
    g = rng.normal(0.002 + edge, 0.03, n)
    t = pd.DataFrame({"rule": rng.choice(st.RULES, n), "symbol": rng.choice(["AAA", "BBB", "CCC"], n),
                      "session": [f"2026-01-{d:02d}" for d in rng.integers(1, 29, n)], "k": rng.integers(0, 4, n),
                      "entry": rng.uniform(1, 12, n), "gross": g, "cost": 0.009, "minutes": rng.integers(1, 40, n),
                      "minute": rng.integers(570, 950, n)})
    t["net"] = t.gross - t.cost
    return t


def test_gate_is_on_net_per_round_trip_and_the_gross_is_reported_beside_it():
    r = st.evaluate(table(), 500)
    d = r["rules"]["TREND"]
    assert r["passes"] == [] and d["pass"] is False and d["gross"] > 0 > d["net"]
    assert d["net_at_careful_cost"] == pytest.approx(d["gross"] - st.CAREFUL_COST) and d["round_trips_per_stock_day"] > 1
    good = st.evaluate(table(edge=0.02), 500)
    assert set(good["passes"]) == set(st.RULES) and good["alpha_each"] == st.ALPHA / 2
    one = st.evaluate(table(edge=0.02), 500, only=["TREND"])
    assert one["passes"] == ["TREND"] and "pass" not in one["rules"]["TREND2"] and one["alpha_each"] == st.ALPHA


def test_guards(tmp_path, capsys):
    assert st.main(["--holdout", "--out", str(tmp_path)]) == 2 and "only for a rule that passed" in capsys.readouterr().out
    (tmp_path / "result_train.json").write_text('{"passes": []}')
    assert st.main(["--holdout", "--out", str(tmp_path)]) == 2
    assert st.main(["--out", str(tmp_path)]) == 2 and "not repeated" in capsys.readouterr().out
