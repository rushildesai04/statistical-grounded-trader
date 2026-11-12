"""patterns.simulate red_exit (prereg_long_risk_2026-11-12 E, adopted for the live long book as A2)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sgt.fast import patterns as pt

T0 = pd.Timestamp("2026-03-02 15:00", tz="UTC")


def bars(rows):
    return pd.DataFrame([{"ts": T0 + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": 1000.0}
                         for i, (o, h, lo, c) in enumerate(rows)])


FLAT = [(10.0, 10.05, 9.95, 10.0)] * 3
S = pt.Setup("L4b", "long", T0 + 3 * pt.BAR, 10.1, 9.9)          # buy-stop 10.10, stop 9.90 -> target 10.50


def test_red_fill_candle_sells_at_the_next_open():
    f = bars(FLAT + [(10.0, 10.3, 9.95, 10.05), (10.02, 10.6, 10.0, 10.5)] + [(10.5, 10.6, 10.4, 10.5)] * 30)
    assert pt.simulate(f, S).reason == "target"                                   # default: unchanged
    t = pt.simulate(f, S, red_exit=True)
    assert (t.reason, t.exit, t.exit_ts) == ("red", 10.02, f.ts[4]) and t.gross == pytest.approx(10.02 / 10.1 - 1)


def test_fill_bar_stop_or_target_win_over_the_red_exit():
    stop = bars(FLAT + [(10.0, 10.2, 9.8, 9.85), (9.9, 10.0, 9.8, 9.9)] * 10)
    assert pt.simulate(stop, S, red_exit=True).reason == "stop"
    tgt = bars(FLAT + [(10.0, 10.6, 9.95, 10.05)] + [(10.0, 10.1, 9.95, 10.0)] * 30)   # closes red but hit 10.50
    assert pt.simulate(tgt, S, red_exit=True).reason == "target"


def test_green_or_flat_fill_candle_runs_the_normal_path():
    for close in (10.1, 10.2):                                                    # close == entry is not below it
        f = bars(FLAT + [(10.0, 10.3, 9.95, close)] + [(10.2, 10.3, 10.1, 10.2)] * 40)
        assert pt.simulate(f, S, red_exit=True) == pt.simulate(f, S)


def test_red_exit_uses_the_next_bar_even_after_a_halt_gap_and_the_close_when_none_is_left():
    f = bars(FLAT + [(10.0, 10.3, 9.95, 10.05)])
    gap = pd.concat([f, bars([(9.7, 9.8, 9.6, 9.7)]).assign(ts=T0 + 50 * pt.BAR)], ignore_index=True)
    t = pt.simulate(gap, S, red_exit=True)                                        # next bar 46 minutes later
    assert (t.reason, t.exit, t.exit_ts) == ("red", 9.7, T0 + 50 * pt.BAR)
    last = pt.simulate(f, S, red_exit=True)
    assert (last.reason, last.exit) == ("red", 10.05)


def test_shorts_never_take_the_red_exit():
    s = pt.Setup("S2", "short", T0 + 3 * pt.BAR, 9.9, 10.1)
    f = bars(FLAT + [(10.0, 10.05, 9.85, 9.95)] + [(9.95, 10.0, 9.9, 9.95)] * 40)
    assert pt.simulate(f, s, red_exit=True) == pt.simulate(f, s)


def test_matches_the_study_rule_on_random_paths():
    """The live rule equals the tested one (scripts/long_risk_study.red_exit applied to the BASE trade)."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import long_risk_study as lr
    rng = np.random.default_rng(11)
    checked = changed = 0
    for _ in range(400):
        c = 10 + np.cumsum(rng.normal(0, 0.06, 60))
        o = np.r_[c[0], c[:-1]] + rng.normal(0, 0.02, 60)
        f = pd.DataFrame({"ts": [T0 + i * pt.BAR for i in range(60)], "open": o,
                          "high": np.maximum(o, c) + rng.uniform(0, 0.08, 60),
                          "low": np.minimum(o, c) - rng.uniform(0, 0.08, 60), "close": c, "volume": 1000.0})
        s = pt.Setup("L4b", "long", f.ts[3], float(c[2]) + 0.05, float(c[2]) - 0.15)
        base = pt.simulate(f, s)
        if base is None:
            continue
        g, ch = lr.red_exit(f, base, int(np.flatnonzero((f.ts == base.entry_ts).to_numpy())[0]))
        assert pt.simulate(f, s, red_exit=True).gross == pytest.approx(g)
        checked += 1
        changed += ch
    assert checked > 200 and changed > 20
