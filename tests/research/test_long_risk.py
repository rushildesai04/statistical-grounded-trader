"""Logic of prereg_long_risk_2026-11-12 on synthetic bars (run before the single study run)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_risk_study as lr  # noqa: E402

from sgt.fast import patterns as pt  # noqa: E402

T0 = pd.Timestamp("2026-03-02 15:00", tz="UTC")


def frame(rows):
    """rows = (open, high, low, close) per minute from T0."""
    return pd.DataFrame({"ts": [T0 + i * pt.BAR for i in range(len(rows))], "open": [r[0] for r in rows],
                         "high": [r[1] for r in rows], "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                         "volume": 1000.0})


def test_risk_fields_stop_distance_and_gap_in_planned_r():
    s = pt.Setup("L4b", "long", T0, 10.0, 9.5)
    t = pt.Trade("L4b", "long", T0, 11.0, T0, 11.0, "time", 0.0)          # opened 1.0 above the 10.0 trigger
    f = lr.risk_fields(s, t)
    assert f["d"] == pytest.approx(1.5 / 11.0) and f["g"] == pytest.approx(2.0)
    m = lr.risk_fields(pt.Setup("L7a", "long", T0, None, 9.0), pt.Trade("L7a", "long", T0, 10.0, T0, 10.0, "time", 0.0))
    assert m["g"] == 0.0 and m["trigger"] is None and m["d"] == pytest.approx(0.1)


def test_red_exit_sells_next_open_only_when_the_fill_candle_closes_below_entry():
    f = frame([(10.0, 10.2, 9.9, 9.95), (9.90, 10.0, 9.8, 9.9), (9.9, 10.0, 9.8, 9.9)])
    t = pt.Trade("L4b", "long", f.ts[0], 10.0, f.ts[2], 9.9, "time", -0.01)
    g, changed = lr.red_exit(f, t, 0)
    assert changed and g == pytest.approx(9.90 / 10.0 - 1)
    up = frame([(10.0, 10.2, 9.9, 10.05), (9.90, 10.0, 9.8, 9.9)])
    assert lr.red_exit(up, pt.Trade("L4b", "long", up.ts[0], 10.0, up.ts[1], 9.9, "time", -0.01), 0) == (-0.01, False)
    eq = frame([(10.0, 10.2, 9.9, 10.0), (9.90, 10.0, 9.8, 9.9)])                  # close == entry: not below
    assert lr.red_exit(eq, pt.Trade("L4b", "long", eq.ts[0], 10.0, eq.ts[1], 9.9, "time", -0.01), 0)[1] is False


def test_red_exit_leaves_fill_bar_exits_alone_and_uses_the_close_on_the_last_bar():
    f = frame([(10.0, 10.2, 9.0, 9.1), (9.1, 9.2, 9.0, 9.1)])
    stopped = pt.Trade("L4b", "long", f.ts[0], 10.0, f.ts[0], 9.5, "stop", -0.05)       # stop on the fill bar
    assert lr.red_exit(f, stopped, 0) == (-0.05, False)
    last = frame([(10.0, 10.2, 9.9, 9.8)])
    t = pt.Trade("L4b", "long", last.ts[0], 10.0, last.ts[0] + pt.BAR, 9.8, "time", -0.02)
    assert lr.red_exit(last, t, 0) == (pytest.approx(-0.02), True)


def _book(n=40, seed=3):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"session": [f"2026-01-{1 + i % 20:02d}" for i in range(n)], "net": rng.normal(-0.02, 0.04, n),
                         "d": rng.uniform(0.01, 0.15, n), "trigger": [10.0 if i % 2 else None for i in range(n)],
                         "g": rng.uniform(0, 2, n)})


def test_wide_stop_skip_skips_strictly_above_the_threshold():
    b = _book()
    st = lr.w_stats(b, float(b.d.iloc[0]))
    assert st["n_skipped"] == int((b.d > b.d.iloc[0]).sum()) and st["n_kept"] + st["n_skipped"] == len(b)
    assert st["kept"] == pytest.approx(b[b.d <= b.d.iloc[0]].net.mean())


def test_gap_skip_never_skips_market_entries_and_keeps_exactly_one_r():
    b = pd.DataFrame({"session": ["a", "a", "b", "c"], "net": [0.1, -0.2, 0.0, -0.1],
                      "d": 0.05, "trigger": [None, 10.0, 10.0, 10.0], "g": [5.0, 1.0, 1.01, 0.0]})
    st = lr.g_stats(b)
    assert st["n_skipped"] == 1 and st["skipped"] == pytest.approx(0.0)


def test_risk_sizing_weights_cap_at_one_and_shrink_wide_stops():
    d = pd.Series([0.02, 0.05, 0.10])
    assert list(lr.weights(d, 0.05)) == pytest.approx([1.0, 1.0, 0.5])
    b = pd.DataFrame({"session": ["a", "b", "c", "d"], "net": [0.02, 0.02, -0.10, -0.10], "d": [0.02, 0.02, 0.2, 0.2]})
    st = lr.r_stats(b, 0.02)                      # wide-stop losers get 0.1 weight: per-dollar return improves
    assert st["sized"] == pytest.approx((0.02 + 0.02 - 0.01 - 0.01) / 2.2) and st["diff"] > 0
    same = lr.r_stats(b.assign(d=0.01), 0.02)     # all tighter than the median: equal weights, no difference
    assert same["diff"] == pytest.approx(0.0)


def test_e_stats_counts_changed_trades_and_pairs_the_difference():
    b = pd.DataFrame({"session": ["a", "a", "b"], "net": [-0.05, 0.02, -0.05], "e_net": [-0.02, 0.02, -0.05],
                      "e_changed": [True, False, False]})
    st = lr.e_stats(b)
    assert st["changed"] == 1 and st["diff"] == pytest.approx(0.01) and st["changed_e"] == pytest.approx(-0.02)


def test_training_pass_needs_power_a_positive_difference_and_bonferroni_p():
    ok = {"n_kept": 500, "n_skipped": 150, "diff": 0.01, "p": 0.01}
    assert lr.train_pass("W", ok)
    assert not lr.train_pass("W", {**ok, "p": 0.013})                     # 0.05/4 = 0.0125
    assert not lr.train_pass("G", {**ok, "n_skipped": 99})
    assert not lr.train_pass("R", {"n": 2000, "diff": -0.001, "p": 0.0})
    assert not lr.train_pass("E", {"n": 2000, "changed": 99, "diff": 0.02, "p": 0.0})
    assert lr.train_pass("E", {"n": 2000, "changed": 100, "diff": 0.02, "p": 0.0})


def test_profitable_parts_read_the_adopted_variant():
    assert lr.profitable_parts("R", {"sized": 0.01, "sized_lower95": -0.01}) == (0.01, -0.01)
    assert lr.profitable_parts("G", {"kept": -0.02, "kept_lower95": -0.03}) == (-0.02, -0.03)


def test_event_rows_runs_end_to_end(monkeypatch):
    """Smoke over the per-event path with the real simulator on a red fill candle."""
    rows = [(10.0, 10.05, 9.95, 10.0)] * 5 + [(10.0, 10.3, 9.95, 10.02), (10.0, 10.1, 9.96, 10.05)] + \
           [(10.05, 10.1, 10.0, 10.05)] * 30
    f = frame(rows)
    s = pt.Setup("L4b", "long", f.ts.iloc[5], 10.1, 9.9)
    t = pt.simulate(f, s)
    assert t is not None and t.entry == pytest.approx(10.1) and t.exit_ts != t.entry_ts
    monkeypatch.setattr(lr, "first_fill", lambda *a, **k: (s, t))
    ev = pd.DataFrame({"symbol": ["X"], "session": ["2026-03-02"], "cost_rt": [0.01], "t0": [f.ts.iloc[0]]})
    out = lr.event_rows(ev, lambda sym, ses: f, False)
    assert len(out) == 1 and out[0]["e_changed"] is True                    # fill bar closed 10.02 < 10.10
    assert out[0]["e_net"] == pytest.approx(10.0 / 10.1 - 1 - 0.01) and out[0]["net"] == pytest.approx(t.gross - 0.01)
    assert out[0]["g"] == pytest.approx(0.0) and out[0]["d"] == pytest.approx(0.2 / 10.1)


def test_stats_path_runs_on_a_small_book_without_passing():
    b = _book(60).assign(e_net=lambda x: x.net + 0.001, e_changed=True)
    res = lr.run_all(b, float(b.d.quantile(0.8)), float(b.d.median()))
    assert set(res) == {"W", "R", "G", "E"} and all(0 <= v["p"] <= 1 for v in res.values())
    assert not any(lr.train_pass(k, v) for k, v in res.items())             # under the 100 minimums
    sec = lr.secondary(b)
    assert sum(q["n"] for q in sec["d_quintiles"].values()) == len(b)


def test_p_counts_undefined_resamples_against_the_candidate():
    assert lr._p(np.array([0.1, np.nan, 0.2, -0.1])) == pytest.approx(0.5)
