"""Mechanics of the institutional-flow study (scripts/inst_flow_study.py): no leakage, stops, costs."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import inst_flow_study as st  # noqa: E402


def panel(n=30, syms=("AAA", "BBB")) -> pd.DataFrame:
    days = pd.bdate_range("2022-11-01", periods=n)
    rows = []
    for j, s in enumerate(syms):
        c = 100.0 + j * 10 + np.arange(n)
        rows.append(pd.DataFrame({"symbol": s, "date": days, "open": c - 0.5, "high": c + 1, "low": c - 1,
                                  "close": c, "spy_open": 400.0, "spy_close": 400.0, "eligible": True,
                                  "beta_252": 1.0, "atr_pct": 0.02}))
    return pd.concat(rows, ignore_index=True)


def test_forward_paths_stay_within_symbol():
    p = panel()
    paths = st.forward_paths(p, np.ones(len(p), bool), hmax=3)
    last_aaa = p.index[(p.symbol == "AAA")][-1]
    assert np.isnan(paths["entry"][last_aaa]) and np.isnan(paths["cl3"][last_aaa - 2])   # no spill into BBB
    assert paths["entry"][0] == pytest.approx(p.open[1]) and paths["cl3"][0] == pytest.approx(p.close[3])


def test_training_targets_never_see_holdout_prices():
    class V:
        def train_rows(self, df, col):
            return df[df[col] < pd.Timestamp("2022-12-01")]
    tr, paths = st.eligible_with_paths(st.train_view(V(), panel()))
    tr = st.add_targets(tr, paths)
    last = tr[tr.date == tr.date.max()]
    assert last.y1.isna().all()                                          # its next open is in the held-out span
    near = tr[tr.date == tr.date.sort_values().unique()[-5]]
    assert near.y5.isna().all() and near.y1.notna().all()


def test_trade_returns_stop_gap_hedge_and_costs():
    paths = {"entry": np.array([100.0, 100.0]), "spy_entry": np.array([400.0, 400.0]),
             "op1": np.array([100.0, 100.0]), "hi1": np.array([101.0, 101.0]), "lo1": np.array([99.0, 99.0]),
             "cl1": np.array([100.0, 100.0]), "op2": np.array([90.0, 110.0]), "hi2": np.array([91.0, 111.0]),
             "lo2": np.array([89.0, 109.0]), "cl2": np.array([90.0, 110.0]),
             "spy1": np.array([400.0, 400.0]), "spy2": np.array([404.0, 404.0])}
    rule = st.Rule(h=2, q=0.1, stop=2.0, side="ls")
    r = st.trade_returns(paths, np.array([0, 1]), np.array([1.0, -1.0]), np.array([0.02, 0.02]),
                         np.array([1.0, 1.0]), rule)
    # long: stop 96 gapped through at the 90 open -> -10%; hedge short SPY -1%; costs 20 bp
    assert r[0] == pytest.approx(-0.10 - 0.01 - 2 * st.COST_SIDE)
    # short: stop 104 gapped through at 110 -> -10%; hedge long SPY +1%; costs + 2 days borrow
    assert r[1] == pytest.approx(-0.10 + 0.01 - 2 * st.COST_SIDE - st.BORROW_YR * 2 / 252)
    no_stop = st.trade_returns(paths, np.array([0]), np.array([1.0]), np.array([0.02]), np.array([0.0]),
                               st.Rule(2, 0.1, 0.0, "lh"))
    assert no_stop[0] == pytest.approx(-0.10 - 2 * st.COST_SIDE)


def test_cohort_weights_pick_extremes_and_scale_by_horizon():
    p = panel(n=5, syms=tuple("ABCDEFGHIJ")).reset_index(drop=True)
    p = p[p.date == p.date.min()].reset_index(drop=True)
    pred = np.arange(len(p), dtype=float)
    paths = {"entry": np.full(len(p), 100.0, np.float32), "spy_entry": np.full(len(p), 400.0, np.float32),
             "cl1": np.where(pred == 9, 110.0, np.where(pred == 0, 90.0, 100.0)).astype(np.float32),
             "spy1": np.full(len(p), 400.0, np.float32)}
    c = st.cohort_returns(p, pred, paths, st.Rule(1, 0.1, 0.0, "ls"))
    assert c.iloc[0] == pytest.approx(0.5 * 0.10 + 0.5 * 0.10 - 2 * st.COST_SIDE - 0.5 * st.BORROW_YR / 252)


def test_13f_features_are_as_of_their_available_date(tmp_path):
    agg = pd.DataFrame({"cusip": ["C1", "C1"], "period": ["2022-06-30", "2022-09-30"], "n_holders": [100, 120],
                        "total_shares": [1e6, 1.5e6], "n_new": [0, 30], "n_exit": [0, 10],
                        "available_date": ["2022-08-15", "2022-11-15"]})
    agg.to_parquet(tmp_path / "agg.parquet")
    pd.DataFrame({"cusip": ["C1"], "ticker": ["AAA"]}).to_parquet(tmp_path / "map.parquet")
    p = panel(n=20, syms=("AAA",))
    out = st.f13_features(p, tmp_path / "agg.parquet", tmp_path / "map.parquet")
    before = out[out.date < pd.Timestamp("2022-11-15")]
    after = out[out.date >= pd.Timestamp("2022-11-15")]
    assert before.breadth_chg.isna().all()                               # first period has no prior quarter
    assert np.allclose(after.breadth_chg, 0.2) and np.allclose(after.net_new_share, 0.2)


def test_block_bootstrap_lower_bound():
    rng = np.random.default_rng(1)
    assert st.block_lower(np.full(200, 0.001) + rng.normal(0, 1e-4, 200), rng) > 0
    assert np.isnan(st.block_lower(np.ones(10), rng))
