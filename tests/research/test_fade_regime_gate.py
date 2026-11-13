"""Regime gate (prereg_fade_regime_gate_2026-11-13): decided from earlier sessions only; statistic and thresholds."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_regime_gate_study as g  # noqa: E402


def frame(days):                                        # days: list of lists of nets, one list per session
    rows = [{"symbol": f"S{i}", "session": f"2026-01-{d + 1:02d}", "net": n} for d, nets in enumerate(days) for i, n in enumerate(nets)]
    return pd.DataFrame(rows)


BAD, GOOD = [-0.05, -0.04, 0.01], [0.05, 0.04, -0.01]   # 1 of 3 wins, mean < 0 / 2 of 3 wins, mean > 0


def test_gate_needs_five_earlier_sessions_and_fifteen_trades():
    t = frame([BAD] * 5 + [GOOD])
    assert g.skipped_sessions(t) == {"G1": {"2026-01-06"}, "G2": {"2026-01-06"}}
    assert g.skipped_sessions(frame([BAD] * 4 + [GOOD])) == {"G1": set(), "G2": set()}            # only 4 earlier sessions
    thin = frame([[-0.05, -0.04]] * 5 + [GOOD])                                                    # 10 trades < 15
    assert g.skipped_sessions(thin) == {"G1": set(), "G2": set()}


def test_gate_uses_only_earlier_sessions():
    t = frame([GOOD] * 5 + [BAD] * 6)
    sk = g.skipped_sessions(t)
    # the first bad session is still traded (its look-back is all good); changing today's trades never changes today
    assert "2026-01-06" not in sk["G1"] and "2026-01-11" in sk["G1"]
    t2 = t.copy()
    t2.loc[t2.session == "2026-01-11", "net"] = 0.5
    assert ("2026-01-11" in g.skipped_sessions(t2)["G1"]) and g.skipped_sessions(t2)["G1"] == sk["G1"]


def test_g1_and_g2_differ_when_wins_are_small_and_losses_big():
    mixed = [0.01, 0.01, -0.10]                         # 2 of 3 win, mean < 0
    sk = g.skipped_sessions(frame([mixed] * 5 + [GOOD]))
    assert sk["G1"] == set() and sk["G2"] == {"2026-01-06"}
    exact = [0.02, -0.01]                               # win share exactly 50%: not below -> open
    assert g.skipped_sessions(frame([exact + exact + exact + exact] * 5 + [GOOD]))["G1"] == set()


def test_statistic_is_minus_the_skipped_net_per_trade_and_thresholds_fail_closed():
    t = frame([BAD] * 5 + [BAD] * 40 + [GOOD])
    s = g.stats(t, g.skipped_sessions(t)["G1"])
    skipped = t[t.session.isin(g.skipped_sessions(t)["G1"])]
    assert abs(s["stat"] - (-skipped.net.sum() / len(t))) < 1e-12 and s["stat"] > 0 and s["n_skipped"] == len(skipped)
    assert abs(s["gated_mean"] - s["ungated_mean"] - s["stat"]) < 1e-12
    assert g.train_pass(s)
    assert not g.train_pass({**s, "n_skipped": 99}) and not g.train_pass({**s, "p": 0.025}) and not g.train_pass({**s, "stat": 0.0})
    assert g.stats(t.iloc[:0], set()) == {"n": 0}
