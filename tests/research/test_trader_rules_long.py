"""Logic of prereg_trader_rules_long_2026-11-12 on synthetic bars (run before the single study run)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import trader_rules_long_study as tr  # noqa: E402

from sgt.fast import patterns as pt  # noqa: E402
from sgt.fast import signals  # noqa: E402

T0 = pd.Timestamp("2026-03-02 15:00", tz="UTC")


def bars(close, vol=None, spread=0.02):
    c = np.asarray(close, float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"ts": [T0 + i * pt.BAR for i in range(len(c))], "open": o,
                         "high": np.maximum(o, c) + spread, "low": np.minimum(o, c) - spread, "close": c,
                         "volume": np.full(len(c), 1000.0) if vol is None else np.asarray(vol, float)})


def test_psar_side_per_bar_matches_the_chart_signal_on_every_prefix():
    rng = np.random.default_rng(7)
    f = bars(10 + np.cumsum(rng.normal(0, 0.05, 120)))
    h, lo = f.high.to_numpy(), f.low.to_numpy()
    up = tr.psar_up(h, lo)
    for k in range(1, len(f)):
        assert up[k] == (signals._psar_side(h[:k + 1], lo[:k + 1]) == 1.0), k
    assert not up[0]


def test_volume_spike_needs_5x_the_prior_10_bars_and_lasts_5_bars():
    v = [1000.0] * 12 + [5000.0] + [1000.0] * 8
    c = tr.conditions(bars(np.linspace(10, 10.2, len(v)), v))
    assert list(c.c2.iloc[11:19]) == [False, True, True, True, True, True, False, False]
    z = tr.conditions(bars(np.linspace(10, 10.2, 21), [0.0] * 12 + [5000.0] + [0.0] * 8))
    assert not z.c2.any()                                   # no volume before: no spike (fail closed)


def test_trend_macd_vwap_and_psar_are_true_in_a_steady_rise_and_false_in_a_fall():
    up = tr.conditions(bars(10 * 1.002 ** np.arange(60)))
    assert up[["c1", "c3", "c4", "c5"]].iloc[-1].all()
    dn = tr.conditions(bars(10 * 0.998 ** np.arange(60)))
    assert not dn[["c1", "c3", "c4", "c5"]].iloc[-1].any()


def test_gate_reads_the_bar_before_the_fill_bar_never_the_fill_bar():
    cond = pd.DataFrame({k: [True, False, True] for k in (*tr.COND, "all5")})
    assert tr.gate(cond, 2)["all5"] is False and tr.gate(cond, 1)["all5"] is True
    assert tr.gate(cond, 0)["all5"] is False


def _cond(n, cross_at, ok_from=0):
    d = pd.DataFrame({k: [i >= ok_from for i in range(n)] for k in tr.COND})
    d["cross"] = [i in cross_at for i in range(n)]
    d["hist"] = 0.1
    d["all5"] = d[list(tr.COND)].all(axis=1)
    return d


def test_t5_takes_the_first_qualifying_cross_in_the_watch_window_with_the_k2_low_stop():
    f = bars(np.linspace(10, 11, 50))
    s = tr.t5_setup(f, _cond(50, {3, 8, 12}, ok_from=5), T0 + 2 * pt.BAR)
    assert s.kind == "T5" and s.trigger is None and s.info["signal_i"] == 8
    assert s.ready == f.ts.iloc[8] + pt.BAR
    assert s.stop == pytest.approx(f.low.iloc[6:9].min())
    assert tr.t5_setup(f, _cond(50, {45}), T0) is None    # 45 minutes after the start: outside WATCH


def test_t5_fills_at_the_next_bar_open_through_the_live_simulator():
    f = bars(np.linspace(10, 11, 50))
    s = tr.t5_setup(f, _cond(50, {8}), T0)
    t = pt.simulate(f, s)
    assert t.entry_ts == f.ts.iloc[9] and t.entry == pytest.approx(f.open.iloc[9])


def _xm_bars():
    rows = [(10.0, 10.1, 9.9, 10.0)] * 3 + [(10.6, 11.1, 10.5, 11.0),     # 3 target bar (11.00)
                                             (11.0, 11.4, 10.9, 11.3), (11.3, 11.6, 11.2, 11.5),
                                             (11.4, 11.5, 11.1, 11.2), (11.25, 11.3, 11.0, 11.1)]
    return pd.DataFrame({"ts": [T0 + i * pt.BAR for i in range(len(rows))], "open": [r[0] for r in rows],
                         "high": [r[1] for r in rows], "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                         "volume": 1000.0})


def test_xm_sells_at_the_target_when_macd_was_not_positive_before_the_target_bar():
    f = _xm_bars()
    hist = np.array([0.1, 0.1, -0.1, 0.2, 0.2, 0.2, -0.1, 0.1])
    assert tr.xm_exit(f, hist, 9.5, 10.0, 0.10, 3) == (0.10, "target")


def test_xm_holds_while_macd_positive_and_sells_at_the_next_open_after_it_turns():
    f = _xm_bars()
    hist = np.array([0.1, 0.1, 0.1, 0.2, 0.2, 0.2, -0.1, 0.1])
    g, why = tr.xm_exit(f, hist, 9.5, 10.0, 0.10, 3)
    assert why == "macd" and g == pytest.approx(11.25 / 10 - 1)            # bar 6 closes <= 0 -> bar 7 open


def test_xm_stop_is_checked_first_and_on_the_target_bar_sells_at_the_stop():
    f = _xm_bars()
    f.loc[3, "low"] = 9.4
    hist = np.full(8, 0.1)
    assert tr.xm_exit(f, hist, 9.5, 10.0, 0.10, 3) == (pytest.approx(-0.05), "stop")
    f2 = _xm_bars()
    f2.loc[5, ["open", "low"]] = [9.3, 9.2]                                 # later bar gaps through: min(stop, open)
    assert tr.xm_exit(f2, hist, 9.5, 10.0, 0.10, 3) == (pytest.approx(-0.07), "stop")


def test_xm_rides_to_the_session_close_when_macd_never_turns():
    f = _xm_bars()
    g, why = tr.xm_exit(f, np.full(8, 0.1), 9.5, 10.0, 0.10, 3)
    assert why == "close" and g == pytest.approx(0.11)


def test_group_bootstrap_resamples_sessions_jointly():
    s = np.array(["a", "a", "b", "c"])
    d = tr.boot_groups(s, np.array([1.0, 1.0, 1.0, 1.0]), s, np.array([0.0, 0.0, 0.0, 0.0]))
    assert np.allclose(d, 1.0)
    same = tr.boot_groups(s, np.arange(4.0), s, np.arange(4.0))
    assert np.allclose(same, 0.0)


def test_training_pass_needs_power_a_positive_difference_and_bonferroni_p():
    assert tr.train_pass("F5", {"n_kept": 150, "diff": 0.01, "p": 0.01})
    assert not tr.train_pass("F5", {"n_kept": 150, "diff": 0.01, "p": 0.02})     # 0.05/3 = 0.0167
    assert not tr.train_pass("T5", {"n": 99, "diff": 0.05, "p": 0.0})
    assert not tr.train_pass("XM", {"targets": 300, "diff": -0.01, "p": 0.0})


def test_cross_is_only_the_bar_where_ema9_first_closes_above_ema24():
    c = tr.conditions(bars(np.r_[np.linspace(11, 10, 40), np.linspace(10, 11, 40)]))
    px = pd.Series(np.r_[np.linspace(11, 10, 40), np.linspace(10, 11, 40)])
    e9, e24 = px.ewm(span=9, adjust=False).mean(), px.ewm(span=24, adjust=False).mean()
    first = int(np.flatnonzero((e9 > e24).to_numpy() & (np.arange(80) > 40))[0])
    assert list(np.flatnonzero(c.cross.to_numpy())) == [first]
    assert c.c1.iloc[first:].all()
