"""Plumbing of prereg_earnings_midcap_2026-11-18 on synthetic data (events and the trade are the tested large-cap code)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import earnings_midcap_study as st  # noqa: E402


def test_universe_rule_is_mid_size_liquid_and_not_a_large_cap():
    s = pd.DataFrame({"symbol": ["MID", "TINY", "HUGE", "PENNY", "NEW", "AAPL", "EDGE"],
                      "n16": [252, 252, 252, 252, 150, 252, 200],
                      "dv16": [40e6, 2e6, 900e6, 40e6, 40e6, 40e6, 10e6],
                      "px16": [25.0, 25.0, 25.0, 3.0, 25.0, 25.0, 5.0]})
    assert list(st.screen(s).symbol) == ["EDGE", "MID"]
    assert "AAPL" in st.LARGE and len(st.LARGE) == 99          # the earlier study's ticker list (its text says 98)


def test_daily_bars_parse_strictly():
    good = {"t": "2020-01-02T05:00:00Z", "o": 10.0, "h": 11.0, "l": 9.5, "c": 10.5, "v": 1}
    f = st.parse_daily([dict(good, t="2020-01-03T05:00:00Z"), good, good, dict(good, o="10"), dict(good, c=0), dict(good, h=True),
                        {"t": 5}, "junk", None])
    assert list(f.index.strftime("%Y-%m-%d")) == ["2020-01-02", "2020-01-03"] and list(f.columns) == ["open", "high", "low", "close"]
    assert st.parse_daily(None).empty and st.parse_daily({"bars": 1}).empty
    assert len(st.parse_daily([dict(good, t=f"2020-01-{d:02d}T05:00:00Z") for d in range(1, 29)] * 200)) == 28


def prices(n=900, drift=0.0, seed=3, start="2017-01-03"):
    """Event prices as ``event_prices`` returns them: a reaction on R, then ``drift`` x sign over five sessions."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start, periods=1500)
    R = pd.DatetimeIndex(rng.choice(days[:1400], n))
    x = rng.choice([-1, 1], n) * rng.uniform(0.0, 0.25, n)
    c_1 = np.full(n, 100.0); c0 = c_1 * (1 + x)
    c5 = c0 * (1 + np.sign(x) * drift + rng.normal(0, 0.03, n))
    p = pd.DataFrame({"R": R, "open_R": c0, "s_open_R": 400.0, "c_1": c_1, "s_c_1": 400.0, "c0": c0, "s_c0": 400.0,
                      "c1": c0, "s_c1": 400.0, "c3": c0, "s_c3": 400.0, "c5": c5, "s_c5": 400.0,
                      "sym": rng.choice(["AAA", "BBB", "CCC"], n), "dv16": rng.uniform(10e6, 100e6, n)})
    for k in ("_1", "0", "1", "3", "5"):
        p[f"d{k}"] = p.R + pd.Timedelta(days={"_1": -1, "0": 0, "1": 1, "3": 3, "5": 7}[k])
    return p


def test_only_reactions_of_five_percent_trade_in_the_moves_direction_net_of_cost():
    p = prices(drift=0.02)
    r = st.report(p)
    big = (p.c0 / p.c_1 - 1).abs() >= 0.05
    assert r["main"]["n"] == int(big.sum()) and r["events_all"] == len(p)
    gross = (np.sign(p.c0 / p.c_1 - 1) * (p.c5 / p.c0 - 1))[big].mean()
    assert r["main"]["mean"] == pytest.approx(gross - st.COST) and r["mean_2x_cost"] == pytest.approx(gross - 2 * st.COST)
    assert r["long_leg"]["n"] + r["short_leg"]["n"] == r["main"]["n"] and set(r["by_reaction"]) == {"5-10%", "10-20%", "above 20%"}
    assert r["lower_ok"] and st.dev_pass(r)


def test_no_follow_through_does_not_pass_and_a_small_sample_fails_closed():
    assert not st.dev_pass(st.report(prices(drift=0.0)))
    few = st.report(prices(n=120, drift=0.05))
    assert few["lower_ok"] and not st.dev_pass(few)                 # a good mean on under 300 events is not a pass
    empty = st.report(prices(n=50, drift=0.0).assign(c0=100.0))     # no reaction reaches 5%
    assert empty["main"]["n"] == 0 and not empty["lower_ok"] and not st.dev_pass(empty)


def test_window_needs_reaction_and_exit_inside():
    p = prices(n=200)
    w = st.window(p, *st.DEV)
    assert (w.R >= st.DEV[0]).all() and (w.d5 <= st.DEV[1]).all() and len(w) < len(p) or len(w) == len(p)
    late = p.assign(d5=pd.Timestamp("2024-01-05"))
    assert st.window(late, *st.DEV).empty


def test_held_out_needs_all_four_checks():
    r = st.report(prices(n=900, drift=0.03, start="2024-01-02"))
    h = st.hold_pass(r)
    assert h["pass"] and all(h["checks"].values())
    weak = dict(r, lower_ok=False)
    assert not st.hold_pass(weak)["pass"]
    one_year = dict(r, by_year={2024: {"n": 500, "mean": 0.01}, 2025: {"n": 200, "mean": -0.01}, 2026: {"n": 200, "mean": -0.02}})
    assert not st.hold_pass(one_year)["pass"]


def test_guards(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st, "OUT", tmp_path)
    assert st.main(["score"]) == 2 and "only after a development PASS" in capsys.readouterr().out
    (tmp_path / "result_dev.json").write_text('{"pass": false}')
    assert st.main(["score"]) == 2
    assert st.main(["dev"]) == 2 and "not repeated" in capsys.readouterr().out
    (tmp_path / "result_holdout.json").write_text("{}")
    assert st.main(["score"]) == 2 and "not repeated" in capsys.readouterr().out
