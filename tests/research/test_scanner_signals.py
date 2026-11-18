"""Plumbing of prereg_scanner_signals_2026-11-18 on synthetic candles (the study itself runs once on training)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import scanner_signals_study as st  # noqa: E402

N = 390
MIN = np.arange(N) + 570                                      # 09:30 .. 15:59 ET


def quiet(seed=0, n=N):
    """A calm day around 100 with small noise and steady volume."""
    rng = np.random.default_rng(seed)
    c = 100 + np.cumsum(rng.normal(0, 0.01, n))
    o = np.r_[c[0], c[:-1]]
    return o, np.maximum(o, c) + 0.005, np.minimum(o, c) - 0.005, c, np.full(n, 1000.0)


def sig(o, h, lo, c, v, minute=MIN):
    return {(r["kind"], r["dir"]): r for r in st.day_signals(o, h, lo, c, v, minute)}


def test_volume_spike_needs_both_volume_and_a_move_and_takes_the_candles_direction():
    o, h, lo, c, v = quiet()
    c[100:] += 0.30; o[101:] += 0.30; h[100:] += 0.30; lo[101:] += 0.30     # one candle jumps 0.3%
    assert ("spike", 1) not in sig(o, h, lo, c, v)                            # no volume: not a spike
    v[100] = 6000
    s = sig(o, h, lo, c, v)[("spike", 1)]
    assert s["minute"] == MIN[100] and s["g_15"] == pytest.approx(c[115] / o[101] - 1) and s["g_close"] == pytest.approx(c[-1] / o[101] - 1)
    c2 = c.copy(); c2[100:] -= 0.60
    assert ("spike", -1) in sig(o, h, lo, c2, v) and ("spike", 1) not in sig(o, h, lo, c2, v)


def test_a_down_signal_is_scored_as_a_short():
    o, h, lo, c, v = quiet(1)
    c[100:] -= 0.30; o[101:] -= 0.30; lo[100:] -= 0.30; h[101:] -= 0.30
    v[100] = 6000
    s = sig(o, h, lo, c, v)[("spike", -1)]
    assert s["g_60"] == pytest.approx(-(c[160] / o[101] - 1))


def test_new_extreme_needs_thirty_candles_and_double_volume():
    o, h, lo, c, v = quiet(2)
    top = h[:200].max()
    c[200] = top + 0.05; h[200] = top + 0.06
    assert ("extreme", 1) not in sig(o, h, lo, c, v)
    v[200] = 2500
    assert sig(o, h, lo, c, v)[("extreme", 1)]["minute"] == MIN[200]
    o2, h2, lo2, c2, v2 = quiet(2)
    c2[20] = h2[:20].max() + 0.05; h2[20] = c2[20] + 0.01; v2[20] = 2500     # 09:50: too early in the day
    s = sig(o2, h2, lo2, c2, v2)
    assert ("extreme", 1) not in s or s[("extreme", 1)]["minute"] != MIN[20]


def test_burst_is_five_candles_of_one_way_movement_against_the_stocks_own_noise():
    o, h, lo, c, v = quiet(3)
    for k in range(5):
        c[150 + k:] += 0.06
    o = np.r_[c[0], c[:-1]]; h = np.maximum(o, c) + 0.005; lo = np.minimum(o, c) - 0.005
    s = sig(o, h, lo, c, v)
    assert ("burst", 1) in s and 150 <= s[("burst", 1)]["minute"] - 570 <= 154


def test_cross_needs_volume_and_only_the_first_of_the_day_counts():
    n = N
    c = np.r_[np.linspace(100, 99, 100), np.linspace(99, 101, 100), np.linspace(101, 99.5, 100), np.linspace(99.5, 101, n - 300)]
    o = np.r_[c[0], c[:-1]]; h = np.maximum(o, c) + 0.005; lo = np.minimum(o, c) - 0.005; v = np.full(n, 1000.0)
    assert ("cross", 1) not in sig(o, h, lo, c, v)                            # crosses happen, none on volume
    v[:] = 1000.0
    cl = pd.Series(c); above = (cl.ewm(span=9, adjust=False).mean() > cl.ewm(span=21, adjust=False).mean()).to_numpy()
    ups = [i for i in range(46, n) if above[i] and not above[i - 1]]
    assert len(ups) >= 2
    v[ups[0]] = v[ups[1]] = 4000
    s = sig(o, h, lo, c, v)
    assert s[("cross", 1)]["minute"] == MIN[ups[0]]                           # the second cross is not a new signal


def test_only_candles_inside_the_window_signal_and_short_days_are_skipped():
    o, h, lo, c, v = quiet(4)
    c[5:] += 0.5; v[5] = 9000                                                 # 09:35: before the window
    c[380:] += 0.5; v[380] = 9000                                             # 15:50: after it
    s = st.day_signals(o, np.maximum(o, c) + 0.005, np.minimum(o, c) - 0.005, c, v, MIN)
    assert all(st.FIRST_MIN <= r["minute"] <= st.LAST_MIN for r in s)
    assert not any(r["kind"] == "spike" for r in s)
    assert st.day_signals(*quiet(5, n=40), MIN[:40]) == []


def table(n=4000, edge=0.0, seed=7):
    rng = np.random.default_rng(seed)
    days = np.array([f"2020-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 26)])
    t = pd.DataFrame({"symbol": rng.choice(["AAA", "BBB"], n), "day": rng.choice(days, n), "cost": 0.0004, "kind": "spike",
                      "dir": rng.choice([1, -1], n), "minute": rng.integers(585, 925, n)})
    for e in st.EXITS:
        t[f"g_{e}"] = rng.normal(0, 0.003, n)
    t.loc[t.dir == 1, "g_60"] += edge
    return t


def test_a_cell_passes_follow_or_fade_only_with_a_bound_above_zero():
    alpha = st.ALPHA / 48
    flat = st.evaluate(table(), 300, alpha)
    assert flat["passes"] == [] and set(flat["cells"]) == {f"spike {d} | {e}" for d in ("up", "down") for e in st.EXITS}
    good = st.evaluate(table(edge=0.002), 300, alpha)
    assert good["passes"] == ["spike up | 60 | follow"]
    bad = st.evaluate(table(edge=-0.002), 300, alpha)
    assert bad["passes"] == ["spike up | 60 | fade"]
    c = good["cells"]["spike up | 60"]
    assert c["follow_net"] == pytest.approx(c["gross"] - 0.0004) and c["fade_net"] == pytest.approx(-c["gross"] - 0.0004)
    assert c["follow_net_2x"] == pytest.approx(c["gross"] - 0.0008)


def test_small_cells_fail_closed_and_costs_differ_by_stock():
    r = st.evaluate(table(n=200, edge=0.05), 300, st.ALPHA / 48)
    assert r["passes"] == [] and r["cells"]["spike up | 60"]["follow_lower"] is None
    assert st.cost("RDDT") == 0.0010 and st.cost("NVDA") == 0.0004 and st.cost("AAOI") == 0.0030


def test_held_out_scores_only_what_passed(tmp_path, capsys):
    r = st.evaluate(table(edge=0.002, seed=11), 300, st.ALPHA, only=["spike up | 60 | follow"])
    assert list(r["cells"]) == ["spike up | 60"] and r["passes"] == ["spike up | 60 | follow"]
    r2 = st.evaluate(table(edge=-0.002, seed=11), 300, st.ALPHA, only=["spike up | 60 | follow"])
    assert r2["passes"] == []                                                 # the fade side was not what passed
    assert st.main(["--holdout", "--out", str(tmp_path)]) == 2 and "only for something that passed" in capsys.readouterr().out
    (tmp_path / "result_train.json").write_text("{}")
    assert st.main(["--out", str(tmp_path)]) == 2 and "not repeated" in capsys.readouterr().out
