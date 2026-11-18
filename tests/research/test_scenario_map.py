"""Plumbing of prereg_scenario_map_2026-11-18 on synthetic bars (the study itself runs once on training)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import scenario_map_study as st  # noqa: E402

from sgt.fast import patterns as pt  # noqa: E402

T0 = pd.Timestamp("2026-01-05 15:00", tz="UTC")
CUT = T0 + pd.Timedelta(hours=4)


def bars(rows, start):
    return pd.DataFrame([{"ts": start + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": 100, "segment": "rth"}
                         for i, (o, h, lo, c) in enumerate(rows)])


def test_state_uses_only_bars_before_the_alert():
    before = bars([(1.00, 1.05, 0.98, 1.02), (1.02, 1.30, 1.02, 1.25), (1.25, 1.50, 1.20, 1.40), (1.40, 1.42, 1.30, 1.32),
                   (1.32, 1.36, 1.28, 1.35)], T0 - 5 * pt.BAR)
    at = bars([(1.35, 9.99, 0.01, 1.35)], T0)                    # the alert bar's own range must not count
    s = st.state(pd.concat([before, at], ignore_index=True), T0)
    assert s["px"] == 1.35 and s["run"] == pytest.approx(1.35 / 0.98 - 1) and s["off"] == pytest.approx(1.35 / 1.50 - 1)
    assert s["mins_since_high"] == 3.0 and s["swing"] == pytest.approx(np.median([0.07 / 1.02, 0.28 / 1.25, 0.30 / 1.40, 0.12 / 1.32, 0.08 / 1.35]))
    assert st.state(pd.concat([before.iloc[:4], at], ignore_index=True), T0) is None      # fewer than 5 bars before
    assert st.state(before, T0) is None                                                   # no bar at the alert


def test_forward_long_is_open_to_close_minus_cost():
    rth = bars([(10.0, 10.5, 9.8, 10.2)] + [(10.2, 10.4, 10.0, 10.3)] * 3 + [(10.3, 11.0, 9.0, 10.6)] + [(10.6, 10.7, 10.5, 10.6)] * 60, T0)
    f = st.forward(rth, T0, 10.0, 0.01)
    assert f["long_5"] == pytest.approx(10.6 / 10 - 1 - 0.01) and f["long_30"] == pytest.approx(10.6 / 10 - 1 - 0.01)
    assert f["up_30"] == pytest.approx(0.10) and f["down_30"] == pytest.approx(-0.10)
    assert np.isnan(st.forward(rth.iloc[:0], T0, 10.0, 0.01)["long_5"])


# green alert bar, RED candle (low 9.50), then candles for the two entries
def day(after_red):
    return bars([(10.0, 10.2, 9.9, 10.1), (10.1, 10.15, 9.50, 9.60)] + after_red + [(10.4, 10.45, 10.35, 10.4)] * 40, T0)


def test_now_is_stopped_on_its_entry_bar_where_hold_never_enters():
    rth = day([(9.60, 9.70, 9.40, 9.45)])                        # the next candle breaks the red low
    e = st.entries(rth, T0, CUT, 0.01)
    assert e["has_red"] and e["now"] == pytest.approx(9.50 / 9.60 - 1 - 0.01) and e["now_fill_bar_stop"] == 1.0
    assert np.isnan(e["hold1"]) and np.isnan(e["hold2"])


def test_hold_enters_after_the_low_held_with_the_same_stop_and_a_2r_target():
    rth = day([(9.60, 9.80, 9.55, 9.75), (9.75, 9.90, 9.70, 9.85), (9.85, 10.60, 9.80, 10.5)])
    e = st.entries(rth, T0, CUT, 0.01)
    assert e["hold1"] == pytest.approx((9.75 + 2 * 0.25) / 9.75 - 1 - 0.01)       # in at 9.75, target 10.25
    assert e["hold2"] == pytest.approx((9.85 + 2 * 0.35) / 9.85 - 1 - 0.01)       # in at 9.85, target 10.55
    assert e["now"] == pytest.approx((9.60 + 2 * 0.10) / 9.60 - 1 - 0.01) and e["now_fill_bar_stop"] == 0.0


def test_hold2_needs_both_candles_to_hold():
    rth = day([(9.60, 9.80, 9.55, 9.75), (9.75, 9.80, 9.45, 9.50), (9.50, 9.55, 9.30, 9.35)])
    e = st.entries(rth, T0, CUT, 0.01)
    assert not np.isnan(e["hold1"]) and np.isnan(e["hold2"])
    assert e["hold1"] == pytest.approx(9.50 / 9.75 - 1 - 0.01) and e["hold1_fill_bar_stop"] == 1.0


def test_no_red_candle_no_entry():
    rth = bars([(10.0, 10.2, 9.9, 10.1)] * 45, T0)
    e = st.entries(rth, T0, CUT, 0.01)
    assert not e["has_red"] and np.isnan(e["now"]) and np.isnan(e["hold1"])


def table(n=600, seed=2, long_edge=0.0):
    rng = np.random.default_rng(seed)
    run = rng.uniform(0.02, 0.9, n)
    off = np.where(rng.random(n) < 0.5, -0.005, -0.10)
    t = pd.DataFrame({"symbol": "AAA", "session": [f"2026-01-{d:02d}" for d in rng.integers(1, 29, n)], "alert_type": "new_high",
                      "news": rng.random(n) < 0.2, "short": rng.normal(0.01, 0.05, n), "cost": 0.008, "minute": rng.integers(570, 950, n),
                      "px": rng.uniform(1, 12, n), "run": run, "off": off, "mins_since_high": rng.uniform(0, 100, n),
                      "swing": rng.uniform(0.01, 0.1, n), "up_30": 0.05, "down_30": -0.05, "has_red": True})
    for h in st.HORIZONS:
        t[f"long_{h}"] = rng.normal(-0.02, 0.05, n)
    early_high = (run <= np.quantile(run, 1 / 3)) & (off >= st.AT_HIGH)
    t.loc[early_high, "long_30"] += long_edge
    for k in ("now", "hold1", "hold2"):
        t[k] = rng.normal(-0.01, 0.03, n)
        t[f"{k}_fill_bar_stop"] = 0.0
    return t


def test_scenarios_are_run_terciles_by_position():
    t, edges = st.assign(table())
    assert set(t.scenario) == {f"{b}, {w}" for b in st.BANDS for w in ("at the high", "off the high")}
    assert t[t.band == "early"].run.max() <= edges[0] < t[t.band == "middle"].run.min()
    fixed, e2 = st.assign(table(seed=9), edges)                 # held-out use: the training edges are kept
    assert e2 == edges


def test_only_a_scenario_with_a_real_long_edge_is_called_one():
    t, _ = st.assign(table(long_edge=0.06))
    res = st.evaluate(t)
    assert res["long_scenarios"] == ["early, at the high"] and res["scenarios"]["late, off the high"]["is_long"] is False
    t0, _ = st.assign(table(long_edge=0.0))
    r0 = st.evaluate(t0)
    assert r0["long_scenarios"] == [] and r0["hold_pass"] == []


def test_hold_passes_only_when_it_is_profitable_not_merely_better():
    t, _ = st.assign(table())
    t["hold1"] = t.now + 0.005                                   # better than NOW, still losing
    r = st.evaluate(t)
    assert r["hold"]["hold1"]["vs_now_per_event"]["mean"] == pytest.approx(0.005) and not r["hold"]["hold1"]["pass"]
    t["hold2"] = 0.02 + np.random.default_rng(1).normal(0, 0.01, len(t))
    assert st.evaluate(t)["hold_pass"] == ["hold2"]


def test_small_cells_fail_closed():
    t, _ = st.assign(table(n=60, long_edge=0.5))
    assert st.evaluate(t)["long_scenarios"] == []                # every cell is under 40 events


def test_a_second_run_is_refused(tmp_path, capsys):
    (tmp_path / "result_train.json").write_text("{}")
    assert st.main(["--out", str(tmp_path)]) == 2 and "not repeated" in capsys.readouterr().out
