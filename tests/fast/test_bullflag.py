"""Hand-built bar sequences for the owner's bull-flag port (sgt.fast.bullflag; prereg_bull_flag_finder_2026-11-13).

Every expected signal (and its strength) is computed by hand below. A warmup of 20 flat bars (OHLC 10.0, volume 100)
makes ATR -> 0 and the 20-bar volume SMA = 100, so each case controls the pole, flag, breakout and volume spike
directly. The mutation block flips four rule lines in the module source and confirms each flips a fixture's outcome.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pandas as pd
import pytest

from sgt.fast import bullflag as bf

T0 = pd.Timestamp("2026-03-02 14:00", tz="UTC")
WARMUP = [(10.0, 10.0, 10.0, 10.0, 100.0)] * 20          # bars 0..19: flat; ATR -> 0, volSMA -> 100
POLE = [(10.0, 10.4, 10.0, 10.4, 100.0),                 # bars 20..22: 10.0 -> 11.0 (+10%), range 1.0
        (10.4, 10.7, 10.4, 10.7, 100.0),
        (10.7, 11.0, 10.7, 11.0, 100.0)]
FLAG = [(10.90, 10.90, 10.70, 10.75, 50.0),              # bars 23..25: pull back to 10.70, high 10.90, lighter volume
        (10.75, 10.85, 10.72, 10.80, 50.0),
        (10.80, 10.88, 10.75, 10.82, 50.0)]
BREAKOUT = (10.90, 11.20, 10.88, 11.00, 300.0)           # bar 26: close 11.00 > flagHigh 10.90, volume spike


def frame(rows):
    return pd.DataFrame({"ts": [T0 + i * pd.Timedelta(minutes=1) for i in range(len(rows))],
                         "open": [r[0] for r in rows], "high": [r[1] for r in rows], "low": [r[2] for r in rows],
                         "close": [r[3] for r in rows], "volume": [r[4] for r in rows]})


def positive_rows():
    return WARMUP + POLE + FLAG + [BREAKOUT]


# ---- the Wilder ATR and EMA helpers ----------------------------------------------------------------------------
def test_wilder_atr_seeds_with_the_sma_of_the_first_14_true_ranges():
    f = frame(positive_rows())
    atr = bf.wilder_atr(f.high.to_numpy(float), f.low.to_numpy(float), f.close.to_numpy(float))
    assert pd.isna(atr[12]) and atr[13] == pytest.approx(0.0)        # first 13 TRs are 0 -> seed 0, na before
    # bars 20..22 TRs are 0.4, 0.3, 0.3; RMA forward from 0.
    assert atr[20] == pytest.approx(0.4 / 14)
    assert atr[21] == pytest.approx((0.4 / 14 * 13 + 0.3) / 14)
    assert atr[22] == pytest.approx((((0.4 / 14 * 13 + 0.3) / 14) * 13 + 0.3) / 14)


def test_wilder_atr_first_bar_true_range_is_high_minus_low():
    f = frame([(5.0, 6.0, 4.0, 5.5, 100.0)] * 14 + [(5.5, 7.0, 5.0, 6.0, 100.0)])
    atr = bf.wilder_atr(f.high.to_numpy(float), f.low.to_numpy(float), f.close.to_numpy(float))
    # TR[0]=high-low=2.0, TR[1..13]=max(2, |6-5.5|, |4-5.5|)=2.0 -> seed = SMA of fourteen 2.0s = 2.0
    assert atr[13] == pytest.approx(2.0)


def test_ema_matches_alpha_two_over_n_plus_one_seeded_at_the_first_value():
    vals = [10.0, 11.0, 12.0, 11.0]
    e = bf.ema(vals, 3)                                              # alpha = 2/4 = 0.5, seeded at 10
    assert e[0] == pytest.approx(10.0)
    assert e[1] == pytest.approx(0.5 * 11 + 0.5 * 10)
    assert e[2] == pytest.approx(0.5 * 12 + 0.5 * (0.5 * 11 + 0.5 * 10))


# ---- the positive case: one signal with hand-computed fields and strength --------------------------------------
def test_positive_fires_one_signal_with_the_first_passing_fb_pb_pair():
    sigs = bf.find_signals(frame(positive_rows()))
    assert len(sigs) == 1
    s = sigs[0]
    assert (s.i, s.fb, s.pb) == (26, 3, 3)                           # smallest flag then smallest pole that passes
    assert s.flag_high == pytest.approx(10.90) and s.flag_low == pytest.approx(10.70)
    assert s.pole_high == pytest.approx(11.0) and s.pole_low == pytest.approx(10.0)
    assert s.up_move_pct == pytest.approx(10.0)                      # (11-10)/10*100
    assert s.retrace == pytest.approx((11.0 - 10.70) / (1.0 + 0.0001))
    assert s.vol_ratio == pytest.approx(300.0 / 102.5)               # volSMA(20) at bar 26 = 2050/20 = 102.5


def test_positive_strength_matches_the_hand_computed_score():
    s = bf.find_signals(frame(positive_rows()))[0]
    up, retrace, vol_ratio = 10.0, (11.0 - 10.70) / 1.0001, 300.0 / 102.5
    retrace_factor = 1.0 - min(1.0, max(0.0, (retrace - 0.20) / (0.45 - 0.20 + 0.0001)))
    expected = (up * 0.6) + (retrace_factor * 25.0) + (min(3.0, vol_ratio) * 15.0)
    assert expected == pytest.approx(64.9094, abs=1e-3)              # sanity on the hand number
    assert s.strength == pytest.approx(expected)


# ---- negative cases ---------------------------------------------------------------------------------------------
def test_no_signal_when_the_flag_retraces_too_deep():
    deep = [(10.90, 10.90, 10.40, 10.50, 50.0), (10.50, 10.60, 10.40, 10.55, 50.0),
            (10.55, 10.88, 10.42, 10.60, 50.0)]                     # flagLow 10.40 -> retrace ~0.60 > 0.45
    assert bf.find_signals(frame(WARMUP + POLE + deep + [BREAKOUT])) == []


def test_no_signal_when_the_flag_is_too_shallow():
    shallow = [(10.96, 10.97, 10.95, 10.96, 50.0), (10.96, 10.97, 10.95, 10.96, 50.0),
               (10.96, 10.97, 10.95, 10.96, 50.0)]                  # flagLow 10.95 -> retrace ~0.05 < 0.20
    brk = (10.97, 11.20, 10.96, 11.00, 300.0)
    assert bf.find_signals(frame(WARMUP + POLE + shallow + [brk])) == []


def test_no_signal_when_the_pole_is_too_weak():
    weak = [(10.0, 10.07, 10.0, 10.07, 100.0), (10.07, 10.14, 10.07, 10.14, 100.0),
            (10.14, 10.20, 10.14, 10.20, 100.0)]                    # pole +2% < 3%
    flag = [(10.17, 10.18, 10.14, 10.15, 50.0), (10.15, 10.17, 10.14, 10.16, 50.0),
            (10.16, 10.18, 10.15, 10.17, 50.0)]
    brk = (10.18, 10.30, 10.17, 10.25, 300.0)
    assert bf.find_signals(frame(WARMUP + weak + flag + [brk])) == []


def test_no_signal_without_a_volume_spike():
    quiet = (10.90, 11.20, 10.88, 11.00, 110.0)                     # 110 < 1.3 * ~102.5
    assert bf.find_signals(frame(WARMUP + POLE + FLAG + [quiet])) == []


def test_second_breakout_inside_the_cooldown_is_suppressed():
    sigs = bf.find_signals(frame(two_flag_rows()))
    assert [s.i for s in sigs] == [26]                               # bar 33 is 7 bars later: cd still > 0


# ---- a second valid setup 7 bars after the first (for cooldown + mutation) -------------------------------------
POLE2 = [(11.0, 11.4, 11.0, 11.4, 100.0), (11.4, 11.7, 11.4, 11.7, 100.0), (11.7, 12.0, 11.7, 12.0, 100.0)]
FLAG2 = [(11.90, 11.90, 11.70, 11.75, 50.0), (11.75, 11.85, 11.72, 11.80, 50.0), (11.80, 11.88, 11.75, 11.82, 50.0)]
BREAKOUT2 = (11.90, 12.20, 11.88, 12.00, 300.0)                     # bar 33


def two_flag_rows():
    return WARMUP + POLE + FLAG + [BREAKOUT] + POLE2 + FLAG2 + [BREAKOUT2]


# ---- causality: a signal at bar i uses no data after bar i -----------------------------------------------------
def test_signal_does_not_change_when_future_bars_are_appended():
    base = frame(positive_rows())
    early = bf.find_signals(base)
    assert len(early) == 1 and early[0].i == 26
    future = positive_rows() + [(11.0, 14.0, 9.0, 13.5, 9999.0)] * 15     # wild future bars
    later = [s for s in bf.find_signals(frame(future)) if s.i == 26]
    assert len(later) == 1 and later[0] == early[0]                  # the bar-26 signal is byte-for-byte unchanged


def test_prefix_and_full_agree_bar_for_bar():
    full = frame(two_flag_rows())
    all_sigs = bf.find_signals(full)
    for cut in range(27, len(full) + 1):
        prefix = bf.find_signals(full.iloc[:cut].reset_index(drop=True))
        assert [s for s in prefix] == [s for s in all_sigs if s.i < cut]


# ---- the > vs >= boundary (breakout needs a strict close above the flag high) ----------------------------------
def boundary_rows():
    eq = (10.90, 11.00, 10.88, 10.90, 300.0)                        # close 10.90 == flagHigh 10.90: not a breakout
    return WARMUP + POLE + FLAG + [eq]


def test_close_equal_to_flag_high_is_not_a_breakout():
    assert bf.find_signals(frame(boundary_rows())) == []


# ---- mutation check: flip four rule lines in the source, confirm each flips a fixture ---------------------------
_SRC = Path(bf.__file__).read_text()

MUTANTS = {
    "breakout > -> >=": ("close[i] > flag_high", "close[i] >= flag_high", boundary_rows, 0, 1),
    "cooldown 10 -> 1": ("COOLDOWN_BARS = 10", "COOLDOWN_BARS = 1", two_flag_rows, 1, 2),
    "retrace upper 0.45 -> 0.60": ("MIN_RETRACE, MAX_RETRACE = 0.20, 0.45",
                                   "MIN_RETRACE, MAX_RETRACE = 0.20, 0.60",
                                   lambda: WARMUP + POLE + [(10.90, 10.90, 10.40, 10.50, 50.0),
                                                            (10.50, 10.60, 10.40, 10.55, 50.0),
                                                            (10.55, 10.88, 10.42, 10.60, 50.0)] + [BREAKOUT], 0, 1),
    "volume spike 1.3 -> 3.5": ("VOL_SPIKE_MULT = 1.3", "VOL_SPIKE_MULT = 3.5", positive_rows, 1, 0),
}


def _load_mutant(old: str, new: str) -> types.ModuleType:
    mutated = _SRC.replace(old, new, 1)
    assert mutated != _SRC, f"mutation target {old!r} not found in source"
    name = "bullflag_mutant"
    mod = types.ModuleType(name)
    sys.modules[name] = mod                                          # dataclass needs the module in sys.modules
    exec(compile(mutated, bf.__file__, "exec"), mod.__dict__)        # noqa: S102 - trusted local source, test only
    return mod


@pytest.mark.parametrize("name", list(MUTANTS))
def test_each_mutation_changes_a_fixture(name):
    old, new, rows_fn, correct_n, mutant_n = MUTANTS[name]
    f = frame(rows_fn())
    assert len(bf.find_signals(f)) == correct_n, f"baseline wrong for {name}"
    mutant = _load_mutant(old, new)
    assert len(mutant.find_signals(f)) == mutant_n, f"mutation {name} did not change the outcome"
