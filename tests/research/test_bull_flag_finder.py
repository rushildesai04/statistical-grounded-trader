"""Trade-simulation logic of prereg_bull_flag_finder_2026-11-13 on synthetic bars (run before the single study run).

Hand-built frames exercise X1 (stop-first, 2R target, 30-minute exit), X2 (trend exit at the next open, 15:55 cap),
next-open entry, the flag-low skip, one position at a time, and the Bonferroni / fail-closed statistics. The mutation
block flips four rule lines in the study source and confirms each flips a fixture.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import bull_flag_finder_study as bs  # noqa: E402

from sgt.fast import bullflag as bf  # noqa: E402

BAR = pd.Timedelta(minutes=1)
T0 = pd.Timestamp("2026-03-02 15:00", tz="UTC")             # 10:00 ET (EST), well before the 15:55 cap


def frame(rows, t0=T0):
    """rows = (open, high, low, close) per minute; volume is a constant (unused by the trade rules)."""
    return pd.DataFrame({"ts": [t0 + i * BAR for i in range(len(rows))], "open": [r[0] for r in rows],
                         "high": [r[1] for r in rows], "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                         "volume": 1000.0})


def sig(i, flag_low, strength=80.0, flag_high=None):
    fh = flag_low + 0.5 if flag_high is None else flag_high
    return bf.Signal(i=i, ts=None, fb=3, pb=3, flag_high=fh, flag_low=flag_low, pole_high=fh, pole_low=flag_low,
                     up_move_pct=5.0, retrace=0.3, vol_ratio=2.0, strength=strength)


# ---- X1: entry, target, stop-first, 30-minute exit -------------------------------------------------------------
def test_x1_entry_is_the_next_bar_open_and_skips_when_that_open_is_at_or_below_the_flag_low():
    f = frame([(10, 10, 10, 10), (10.0, 10.2, 9.9, 10.1), (10.1, 10.2, 10.0, 10.1)])
    tr = bs.x1_trade(f, sig(0, 9.5), 0.01)
    assert tr is not None and tr["entry"] == pytest.approx(10.0) and tr["entry_ts"] == f.ts.iloc[1]
    assert bs.x1_trade(f, sig(0, 10.0), 0.01) is None                   # open 10.0 == flag_low: skip
    assert bs.x1_trade(f, sig(0, 10.5), 0.01) is None                   # open below the flag low: skip


def test_x1_takes_the_two_r_target_at_the_target_price():
    f = frame([(10, 10, 10, 10), (10.0, 10.2, 9.9, 10.1), (10.1, 11.2, 10.0, 11.0)])
    tr = bs.x1_trade(f, sig(0, 9.5), 0.01)                              # R = 0.5, target = 11.0
    assert tr["reason"] == "target" and tr["exit"] == pytest.approx(11.0)
    assert tr["net"] == pytest.approx(11.0 / 10.0 - 1 - 0.01)


def test_x1_checks_the_stop_before_the_target_inside_a_bar():
    f = frame([(10, 10, 10, 10), (10.0, 10.2, 9.9, 10.1), (10.1, 11.5, 9.4, 10.0)])   # bar hits both; stop wins
    tr = bs.x1_trade(f, sig(0, 9.5), 0.01)
    assert tr["reason"] == "stop" and tr["exit"] == pytest.approx(9.5)
    assert tr["net"] == pytest.approx(9.5 / 10.0 - 1 - 0.01)


def test_x1_time_exit_is_the_close_of_the_last_bar_within_thirty_minutes():
    f = frame([(10, 10, 10, 10), (10.0, 10.1, 9.9, 10.0)] + [(10.0, 10.1, 9.9, 10.05)] * 3)
    tr = bs.x1_trade(f, sig(0, 9.5), 0.0)
    assert tr["reason"] == "time" and tr["exit"] == pytest.approx(10.05) and tr["exit_ts"] == f.ts.iloc[4]


# ---- X2: trend exit, stop, 15:55 cap ---------------------------------------------------------------------------
def _declining():
    rows = [(10, 10.1, 9.9, 10.0)] * 6 + [(9.0, 9.1, 8.9, 9.0)] * 7       # a drop makes EMA9 fall below EMA21
    return frame(rows)


def test_x2_exits_at_the_next_open_after_a_close_with_ema9_below_ema21():
    f = _declining()
    below = bf.ema(f.close.to_numpy(float), 9) < bf.ema(f.close.to_numpy(float), 21)
    first = next(j for j in range(1, len(f)) if below[j])                 # entry bar is index 1
    tr = bs.make_x2(f)(f, sig(0, 8.0), 0.0)                              # stop 8.0 is never touched
    assert tr["reason"] == "trend" and tr["exit_ts"] == f.ts.iloc[first + 1]
    assert tr["exit"] == pytest.approx(float(f.open.iloc[first + 1]))


def test_x2_checks_the_flag_low_stop_first():
    f = frame([(10, 10, 10, 10), (10.0, 10.1, 9.9, 10.0), (10.0, 10.1, 9.4, 9.8)])   # low 9.4 <= stop 9.5
    tr = bs.make_x2(f)(f, sig(0, 9.5), 0.0)
    assert tr["reason"] == "stop" and tr["exit"] == pytest.approx(9.5)


def test_x2_skips_when_the_next_open_is_at_or_below_the_flag_low():
    f = frame([(10, 10, 10, 10), (9.5, 9.6, 9.4, 9.5), (9.5, 9.6, 9.4, 9.5)])
    assert bs.make_x2(f)(f, sig(0, 9.5), 0.0) is None                    # open 9.5 == flag_low


def test_x2_caps_at_the_fifteen_fifty_five_bar_close():
    t0 = pd.Timestamp("2026-03-02 20:50", tz="UTC")                      # 15:50 ET (EST)
    f = frame([(10, 10.1, 9.9, 10.0)] * 5 + [(10.0, 10.6, 9.95, 10.5)] + [(10.5, 10.6, 10.4, 10.5)] * 2, t0=t0)
    cap = f.ts.iloc[5]                                                   # 15:55 ET bar
    assert cap == pd.Timestamp("2026-03-02 20:55", tz="UTC")
    tr = bs.make_x2(f)(f, sig(0, 8.0), 0.0)                             # no stop, no EMA cross: runs to the cap
    assert tr["reason"] == "time" and tr["exit_ts"] == cap and tr["exit"] == pytest.approx(10.5)


# ---- one position at a time ------------------------------------------------------------------------------------
def _stub_exit(f, s, cost):
    """Deterministic: enter at the next open, exit three bars after the signal at that bar's close."""
    exit_i = s.i + 3
    return bs._row(s, f.ts.iloc[s.i + 1], f.open.iloc[s.i + 1], f.ts.iloc[exit_i], f.close.iloc[exit_i], "time",
                   f.close.iloc[exit_i] / f.open.iloc[s.i + 1] - 1, cost)


def test_book_ignores_a_signal_while_a_trade_is_open_and_takes_later_ones():
    f = frame([(10, 10.1, 9.9, 10.0)] * 14)
    signals = [sig(0, 9.0), sig(2, 9.0), sig(10, 9.0)]                   # signal at bar 2 falls inside trade 1
    trades = bs.book(f, signals, _stub_exit, 0.0)
    assert [t["signal_i"] for t in trades] == [0, 10]                    # bar-2 signal ignored (trade open to bar 3)


# ---- statistics: Bonferroni thresholds and the fail-closed p ---------------------------------------------------
def test_train_pass_needs_power_a_positive_mean_and_the_bonferroni_p():
    assert bs.train_pass("B1X1", {"n": 100, "mean": 0.01, "p": 0.01})
    assert not bs.train_pass("B1X1", {"n": 99, "mean": 0.01, "p": 0.01})       # B1 minimum is 100
    assert not bs.train_pass("B1X1", {"n": 100, "mean": 0.01, "p": 0.013})     # 0.05/4 = 0.0125
    assert not bs.train_pass("B1X1", {"n": 100, "mean": 0.0, "p": 0.0})        # mean must be > 0
    assert bs.train_pass("B2X1", {"n": 50, "mean": 0.01, "p": 0.01})           # B2 minimum is 50
    assert not bs.train_pass("B2X1", {"n": 49, "mean": 0.01, "p": 0.01})


def test_p_counts_undefined_resamples_against_the_candidate():
    assert bs._p(np.array([0.1, np.nan, 0.2, -0.1])) == pytest.approx(0.5)


def test_stats_reports_exits_and_winner_loser_means():
    df = pd.DataFrame({"session": ["a", "a", "b"], "net": [0.1, -0.05, -0.02], "reason": ["target", "stop", "time"]})
    st = bs.stats(df)
    assert st["n"] == 3 and st["mean_winner"] == pytest.approx(0.1)
    assert st["mean_loser"] == pytest.approx((-0.05 - 0.02) / 2) and 0 <= st["p"] <= 1


def test_universe_applies_the_gate_and_the_strength_cut():
    sigs = [(sig(0, 9.0, strength=80.0), True), (sig(5, 9.0, strength=50.0), True), (sig(9, 9.0, strength=90.0), False)]
    assert [s.i for s in bs.universe(sigs, "B1", True)] == [0, 5]        # gate on: drop the un-gated bar 9
    assert [s.i for s in bs.universe(sigs, "B2", True)] == [0]           # strength >= 75 as well
    assert [s.i for s in bs.universe(sigs, "B1", False)] == [0, 5, 9]    # secondary 1: no gate


# ---- mutation check: flip four study rule lines, confirm each flips a fixture ----------------------------------
_SRC = Path(bs.__file__).read_text()


def _load_mutant(old: str, new: str) -> types.ModuleType:
    mutated = _SRC.replace(old, new, 1)
    assert mutated != _SRC, f"mutation target {old!r} not found in source"
    name = "bffs_mutant"
    mod = types.ModuleType(name)
    mod.__file__ = bs.__file__
    sys.modules[name] = mod
    exec(compile(mutated, bs.__file__, "exec"), mod.__dict__)            # noqa: S102 - trusted local source, test only
    return mod


def test_mutation_rr_two_to_ten_loses_the_target():
    f = frame([(10, 10, 10, 10), (10.0, 10.2, 9.9, 10.1), (10.1, 11.2, 10.0, 11.0)])
    assert bs.x1_trade(f, sig(0, 9.5), 0.01)["reason"] == "target"
    mut = _load_mutant("RR = 2.0", "RR = 10.0")
    assert mut.x1_trade(f, sig(0, 9.5), 0.01)["reason"] != "target"      # target 15.0 now out of reach


def test_mutation_hold_thirty_to_one_shortens_the_time_exit():
    f = frame([(10, 10, 10, 10), (10.0, 10.1, 9.9, 10.0)] + [(10.0, 10.1, 9.9, 10.05)] * 3)
    assert bs.x1_trade(f, sig(0, 9.5), 0.0)["exit"] == pytest.approx(10.05)
    mut = _load_mutant("HOLD = pd.Timedelta(minutes=30)", "HOLD = pd.Timedelta(minutes=1)")
    assert mut.x1_trade(f, sig(0, 9.5), 0.0)["exit"] == pytest.approx(10.0)   # only the entry bar is in the window


def test_mutation_strength_cut_seventy_five_to_zero_widens_b2():
    sigs = [(sig(0, 9.0, strength=50.0), True)]
    assert bs.universe(sigs, "B2", True) == []
    mut = _load_mutant("STRENGTH_CUT = 75.0", "STRENGTH_CUT = 0.0")
    assert [s.i for s in mut.universe(sigs, "B2", True)] == [0]


def test_mutation_x2_trend_condition_flip_changes_the_exit_bar():
    f = _declining()
    correct = bs.make_x2(f)(f, sig(0, 8.0), 0.0)
    mut = _load_mutant("if below[j]:", "if not below[j]:")
    flipped = mut.make_x2(f)(f, sig(0, 8.0), 0.0)
    assert flipped["exit_ts"] != correct["exit_ts"]                      # the trend trigger moved
