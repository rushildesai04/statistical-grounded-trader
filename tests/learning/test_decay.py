"""Decay-monitor tests (plan §11.4). Each monitor must trigger on a planted
regime break and stay quiet on a stable, seeded sequence."""
from __future__ import annotations

import numpy as np

from sgt.learning.decay import (
    Action,
    CusumMonitor,
    DecaySupervisor,
    DrawdownBudgetMonitor,
    MonitorSignal,
    PSIMonitor,
    RollingExpectancyMonitor,
    SlippageDriftMonitor,
    WinRateMonitor,
)


def test_cusum_quiet_then_demotes_on_downshift():
    rng = np.random.default_rng(0)
    mon = CusumMonitor(validated_expectancy=0.01, validated_std=0.02, k=0.5, h=5.0)
    # Stable at expectancy -> no signal.
    for x in rng.normal(0.01, 0.02, size=200):
        assert mon.update(x) is None
    # Regime break: persistent losses.
    fired = None
    for x in rng.normal(-0.05, 0.02, size=200):
        sig = mon.update(x)
        if sig:
            fired = sig
            break
    assert fired is not None and fired.action is Action.DEMOTE


def test_rolling_expectancy_halves_on_negative_window():
    mon = RollingExpectancyMonitor(validated_expectancy=0.01, window=20, seed=0)
    for _ in range(19):
        assert mon.update(-0.02) is None  # window not full
    sig = mon.update(-0.02)
    assert sig is not None and sig.action is Action.HALVE


def test_rolling_expectancy_quiet_when_meeting_expectancy():
    rng = np.random.default_rng(1)
    mon = RollingExpectancyMonitor(validated_expectancy=0.01, window=20, seed=0)
    signals = [mon.update(x) for x in rng.normal(0.03, 0.005, size=60)]
    assert all(s is None for s in signals)


def test_drawdown_budget_halve_then_disable():
    mon = DrawdownBudgetMonitor(budget=100.0)
    assert mon.update(10.0) is None            # equity 10, peak 10, dd 0
    s1 = mon.update(-60.0)                      # dd 60 -> 60% -> HALVE
    assert s1 is not None and s1.action is Action.HALVE
    s2 = mon.update(-45.0)                      # dd 105 -> >100% -> DISABLE
    assert s2 is not None and s2.action is Action.DISABLE


def test_drawdown_budget_quiet_when_small():
    mon = DrawdownBudgetMonitor(budget=100.0)
    for x in [5.0, -3.0, 4.0, -2.0, 6.0]:
        assert mon.update(x) is None


def test_slippage_drift_halts_when_realized_exceeds_modeled():
    mon = SlippageDriftMonitor(window=20, threshold=1.5)
    sig = None
    for _ in range(20):
        sig = mon.update(0.02, 0.01)  # realized 2x modeled
    assert sig is not None and sig.action is Action.HALT


def test_slippage_drift_quiet_when_matched():
    mon = SlippageDriftMonitor(window=20, threshold=1.5)
    signals = [mon.update(0.011, 0.01) for _ in range(40)]
    assert all(s is None for s in signals)


def test_win_rate_demotes_on_regime_break():
    mon = WinRateMonitor(validated_win_rate=0.6, min_trades=20, alpha=0.01)
    fired = None
    for i in range(40):
        ret = 1.0 if i % 5 == 0 else -1.0  # ~20% win rate vs validated 60%
        sig = mon.update(ret)
        if sig:
            fired = sig
            break
    assert fired is not None and fired.action is Action.DEMOTE


def test_win_rate_quiet_when_on_target():
    rng = np.random.default_rng(2)
    mon = WinRateMonitor(validated_win_rate=0.55, min_trades=20, alpha=0.01)
    signals = [mon.update(1.0 if rng.uniform() < 0.58 else -1.0) for _ in range(60)]
    assert all(s is None for s in signals)


def test_psi_flags_on_distribution_shift():
    rng = np.random.default_rng(3)
    ref = rng.normal(0.0, 1.0, size=1000)
    mon = PSIMonitor(reference=ref, bins=10, window=200, threshold=0.25)
    sig = None
    for x in rng.normal(3.0, 1.0, size=200):  # shifted far right
        sig = mon.update(x) or sig
    assert sig is not None and sig.action is Action.FLAG


def test_psi_quiet_on_same_distribution():
    rng = np.random.default_rng(4)
    ref = rng.normal(0.0, 1.0, size=1000)
    mon = PSIMonitor(reference=ref, bins=10, window=200, threshold=0.25)
    signals = [mon.update(x) for x in rng.normal(0.0, 1.0, size=200)]
    assert all(s is None for s in signals)


def test_supervisor_returns_most_severe():
    cusum = CusumMonitor(0.01, 0.02)
    dd = DrawdownBudgetMonitor(budget=100.0)
    cusum.last_signal = MonitorSignal(Action.DEMOTE, "cusum", "x")
    dd.last_signal = MonitorSignal(Action.DISABLE, "drawdown_budget", "y")
    sup = DecaySupervisor({"cusum": cusum, "drawdown_budget": dd})
    worst = sup.most_severe()
    assert worst is not None and worst.action is Action.DISABLE


def test_supervisor_quiet_when_no_signals():
    sup = DecaySupervisor({"cusum": CusumMonitor(0.01, 0.02)})
    assert sup.most_severe() is None
