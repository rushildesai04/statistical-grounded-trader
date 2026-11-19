"""Plumbing of prereg_trend_matrix_exit_2026-11-18 on synthetic candles (the study itself runs once on training)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import trend_matrix_exit_study as st  # noqa: E402

T0 = pd.Timestamp("2026-01-05 14:30", tz="UTC")


def candles(closes, volume=1000.0):
    c = np.asarray(closes, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"ts": [T0 + pd.Timedelta(minutes=i) for i in range(len(c))], "open": o, "high": np.maximum(o, c) + 0.01,
                         "low": np.minimum(o, c) - 0.01, "close": c, "volume": volume, "segment": "rth"})


def test_a_steady_rise_turns_every_condition_green_and_a_fall_turns_them_red():
    up = st.matrix(candles(np.linspace(10, 12, 120)))
    assert up[list(st.NAMES)].iloc[-1].all()
    turn = st.matrix(candles(np.r_[np.linspace(10, 12, 120), np.linspace(12, 10, 120)]))
    last = turn[list(st.NAMES)].iloc[-1]
    assert not last.any()                                        # after two hours down every condition is red
    macd_red = int(np.flatnonzero(~turn.macd.to_numpy()[120:])[0])
    ema_red = int(np.flatnonzero(~turn.ema.to_numpy()[120:])[0])
    assert macd_red < ema_red                                    # MACD is the first to warn, as the trader says


def test_supertrend_flips_only_on_a_close_through_the_band():
    c = np.r_[np.full(30, 10.0), np.linspace(10, 9.0, 10), np.full(20, 9.0), np.linspace(9.0, 10.5, 10)]
    f = candles(c)
    up = st.supertrend_up(f.high.to_numpy(), f.low.to_numpy(), f.close.to_numpy())
    assert up[:30].all() and not up[45:55].any() and up[-1]


def test_exit_takes_the_stop_first_then_the_signal_at_the_next_open_else_the_close():
    f = candles([10.0, 10.2, 10.4, 10.3, 10.6, 10.8])
    none = np.zeros(6, bool)
    assert st.exit_on(f, none, 1, 10.0, 9.0) == {"exit": 10.8, "i": 5, "why": "close"}
    red = none.copy(); red[3] = True
    assert st.exit_on(f, red, 1, 10.0, 9.0) == {"exit": f.open[4], "i": 4, "why": "signal"}
    last = none.copy(); last[5] = True
    assert st.exit_on(f, last, 1, 10.0, 9.0) == {"exit": 10.8, "i": 5, "why": "signal"}        # no next candle
    assert st.exit_on(f, red, 1, 10.0, 10.25)["why"] == "stop"                                # candle 3 dips under 10.25 first
    x = st.exit_on(f, red, 1, 10.0, 10.25)
    assert x["i"] == 1 or x["exit"] <= 10.25
    gap = f.copy(); gap.loc[2, ["open", "low"]] = [9.0, 8.9]
    assert st.exit_on(gap, none, 1, 10.0, 9.5) == {"exit": 9.0, "i": 2, "why": "stop"}         # opened below the stop
    assert st.exit_on(f, none, 1, 10.0, 10.19)["exit"] == 10.19                               # on the entry candle: at the stop


def table(n=600, seed=5, macd_edge=0.0):
    rng = np.random.default_rng(seed)
    t = pd.DataFrame({"session": [f"2026-01-{d:02d}" for d in rng.integers(1, 29, n)], "kind": rng.choice(["L4a", "L6"], n),
                      "settled": rng.random(n) < 0.7, "green_at_entry": rng.random(n) < 0.3, "net_now": rng.normal(-0.015, 0.04, n)})
    for name in st.EXITS:
        t[f"net_{name}"] = rng.normal(-0.01, 0.05, n)
        t[f"why_{name}"] = rng.choice(["stop", "signal", "close"], n)
        t[f"min_{name}"] = rng.integers(0, 60, n)
        t[f"first_{name}"] = rng.random(n) < 0.3
    t["net_macd"] += macd_edge
    return t


def test_an_exit_passes_only_when_profitable_not_when_it_merely_loses_less():
    r = st.evaluate(table())
    assert r["passes"] == [] and r["macd"]["vs_now"]["mean"] > 0 and r["macd"]["pass"] is False
    good = st.evaluate(table(macd_edge=0.03))
    assert good["passes"] == ["macd"] and good["alpha_each"] == st.ALPHA / 2
    assert set(good["by_setup"]) == {"L4a", "L6"} and good["settled_only"]["now"]["trades"] < good["trades"]


def test_held_out_tests_only_what_passed(tmp_path, capsys):
    r = st.evaluate(table(macd_edge=0.03, seed=8), only=["macd"])
    assert r["passes"] == ["macd"] and "pass" not in r["matrix"] and r["alpha_each"] == st.ALPHA
    assert st.main(["--holdout", "--out", str(tmp_path)]) == 2 and "only for an exit that passed" in capsys.readouterr().out
    (tmp_path / "result_train.json").write_text('{"passes": []}')
    assert st.main(["--holdout", "--out", str(tmp_path)]) == 2
    assert st.main(["--out", str(tmp_path)]) == 2 and "not repeated" in capsys.readouterr().out
