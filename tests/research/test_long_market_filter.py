"""Study plumbing of prereg_long_market_filter_2026-11-16 on synthetic data (the real record is read once, by the run)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_market_filter_study as st  # noqa: E402
from sgt.megacap import rules  # noqa: E402


def days(n, start="2021-01-04"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def test_flag_reads_only_closes_before_the_decision_session():
    d = days(8)
    close = pd.Series([10, 10, 10, 4, 10, 10, 10, 10.0], index=d)        # one low close, on d[3]
    f = rules.below_trend(d, close, n=3)
    # d[3]'s low close is first known for the decision of d[4]; the decision of d[3] itself saw 10, 10, 10
    assert f.tolist() == [False, False, False, False, True, False, False, False]
    # changing the decision day's own close, or any later close, never changes that day's flag
    later = close.copy()
    later.iloc[4:] = 1.0
    assert rules.below_trend(d, later, n=3).iloc[:5].tolist() == f.iloc[:5].tolist()
    # equal to the average is not below; fewer than n closes is never below; the first session is never below
    assert not rules.below_trend(d, pd.Series(10.0, index=d), n=3).any()
    assert not rules.below_trend(d[:3], pd.Series([10, 9, 8.0], index=d[:3]), n=3).any()
    # a session whose previous session has no market close is not below (the book trades)
    gap = close.drop(d[3])
    assert bool(rules.below_trend(d, gap, n=3).loc[d[4]]) is False
    assert rules.below_trend(list(reversed(d)), close, n=3).tolist() == f.tolist()   # input order does not matter


def synth(n=500, seed=4):
    rng = np.random.default_rng(seed)
    d = days(n)
    rows = [{"session": s, "symbol": y, "label": float(rng.normal(0.001, 0.02)), "pred": 0.01, "position": 1,
             "d_rv21": 0.02} for s in d for y in ("AAA", "BBB", "CCC")]
    # market: up for 250 sessions, then down for 250: with n=20 the second half is below its average
    close = pd.Series(np.r_[np.linspace(100, 200, n // 2), np.linspace(200, 100, n - n // 2)], index=d)
    return pd.DataFrame(rows), d, close


def test_losing_below_trend_days_pass_and_winning_ones_fail(monkeypatch):
    monkeypatch.setattr(rules, "TREND", 20)
    t, d, close = synth()
    below = rules.below_trend(d, close, n=20)
    assert 200 < int(below.sum()) < 250 and not below.iloc[:251].any()
    hit = t.session.map(below).astype(bool)
    bad, good = t.copy(), t.copy()
    bad.loc[hit, "label"] = -0.01
    good.loc[hit, "label"] = 0.01
    rb = st.evaluate(bad, st.books_with(bad, below), (d[0], d[-1]), "train")
    rg = st.evaluate(good, st.books_with(good, below), (d[0], d[-1]), "train")
    assert rb["pass"] and rb["verdict"] == "PASS" and rb["mean_diff"] > 0 and rb["p"] < st.ALPHA
    assert not rg["pass"] and rg["verdict"] == "FAIL" and rg["mean_diff"] < 0 and rg["p"] > 0.9
    # the M book holds nothing on a below-trend session and is the base book on every other one
    b = st.books_with(bad, below)
    assert (b["M"].ret[below.reindex(b["M"].index).to_numpy()] == 0).all()
    assert b["M"].ret[~below.reindex(b["M"].index).to_numpy()].equals(b["base"].ret[~below.reindex(b["base"].index).to_numpy()])
    assert rb["below_sessions"] == int(below.sum()) == rb["below_sessions_with_a_call"]
    assert rb["skipped"]["n"] == 3 * int(below.sum()) and rb["kept"]["n"] == 3 * int((~below).sum())
    assert rb["base_mean_day_below"] < 0 < rg["base_mean_day_below"]


def test_too_few_below_sessions_is_no_verdict(monkeypatch):
    t, d, close = synth(n=300)
    below = pd.Series(False, index=d)
    below.iloc[100:150] = True                                            # 50 below sessions: under the 100 needed
    t.loc[t.session.map(below).astype(bool), "label"] = -0.05
    r = st.evaluate(t, st.books_with(t, below), (d[0], d[-1]), "train")
    assert not r["pass"] and r["verdict"].startswith("NO VERDICT") and r["mean_diff"] > 0


def test_training_runs_once_and_the_holdout_stays_closed_without_a_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st, "OUT", tmp_path / "out")
    (tmp_path / "out").mkdir()
    assert st.main(["--holdout"]) == 2 and "stays closed" in capsys.readouterr().out
    (tmp_path / "out" / "result_train.json").write_text(json.dumps({"pass": False}))
    assert st.main(["--holdout"]) == 2
    assert st.main([]) == 2 and "not repeated" in capsys.readouterr().out   # no second training run


def test_market_file_is_refused_when_bad(tmp_path):
    p = tmp_path / "SPY.parquet"
    pd.DataFrame({"date": ["2021-01-04", "2021-01-04"], "close": [1.0, 2.0]}).to_parquet(p)
    with pytest.raises(SystemExit):
        st.load_market(p)
    pd.DataFrame({"date": ["2021-01-04", "2021-01-05"], "close": [1.0, 0.0]}).to_parquet(p)
    with pytest.raises(SystemExit):
        st.load_market(p)
    with pytest.raises(SystemExit):
        st.load_market(tmp_path / "missing.parquet")
    pd.DataFrame({"date": ["2021-01-05", "2021-01-04"], "close": [2.0, 1.0]}).to_parquet(p)
    assert st.load_market(p).index.tolist() == ["2021-01-04", "2021-01-05"]
