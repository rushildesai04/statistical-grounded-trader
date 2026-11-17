"""Study plumbing of prereg_dip_timer_2026-11-17 on synthetic data (the real record is read once, by the run)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import dip_timer_study as st  # noqa: E402


def days(n, start="2020-01-02"):
    return [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]


def test_breadth_is_the_share_of_names_called_long():
    t = pd.DataFrame({"session": ["a", "a", "a", "a", "b", "b"], "position": [1, 0, 1, 0, 0, 0]})
    assert st.breadth(t).to_dict() == {"a": 0.5, "b": 0.0}


def test_the_cut_of_a_day_uses_only_earlier_days():
    d = days(12)
    b = pd.Series([0.1, 0.0, 0.2, 0.1, 0.0, 0.3, 0.9, 0.1, 0.25, 0.0, 0.5, 0.2], index=d)
    s = st.signal(b, q=0.8, min_hist=5)
    assert not s.iloc[:5].any()                                  # fewer than 5 earlier days: no signal
    assert bool(s.iloc[5]) and bool(s.iloc[6])                   # 0.3 >= 80th pct of the first five (0.12); 0.9 too
    assert not bool(s.iloc[8])                                   # 0.25 is under the cut (0.26) once 0.9 is in the history
    later = b.copy()
    later.iloc[7:] = 1.0                                         # nothing from day 7 on can change days 0..6
    assert st.signal(later, q=0.8, min_hist=5).iloc[:7].tolist() == s.iloc[:7].tolist()
    own = b.copy()
    own.iloc[6] = 0.0                                            # a day with no call is never a signal day
    assert not bool(st.signal(own, q=0.8, min_hist=5).iloc[6])


def test_the_naive_dip_rule_marks_the_largest_falls_causally():
    d = days(10)
    r = pd.Series([0.0, 0.01, -0.01, 0.0, 0.01, -0.03, 0.0, -0.005, -0.04, 0.02], index=d)
    dip = st.dips(r, q=0.8, min_hist=5)
    assert dip.tolist() == [False] * 5 + [True, False, False, True, False]


def make(n=400, edge=0.0, seed=1):
    """Twenty names; about a fifth of days are 'call days' (half the names called). ``edge`` is added to the
    market's NEXT-day return on those days."""
    rng = np.random.default_rng(seed)
    d = days(n, "2018-06-01")
    hot = rng.random(n) < 0.2
    rows = [{"session": s, "symbol": f"N{k}", "position": int(hot[i] and k < 10), "label": 0.0}
            for i, s in enumerate(d) for k in range(20)]
    r = rng.normal(0.0003, 0.01, n + 1)
    r[1:][hot] += edge                                           # the return from day i's close to day i+1's close
    close = pd.Series(100 * np.cumprod(1 + r), index=days(n + 1, "2018-06-01"))
    return pd.DataFrame(rows), close, d, hot


def test_frame_aligns_the_next_days_return_with_the_decision_day():
    t, close, d, hot = make(n=320)
    f = st.frame(t, close, d[-1])
    i = 300
    assert f.next.loc[d[i]] == pytest.approx(close.iloc[i + 1] / close.iloc[i] - 1)
    assert f.same.loc[d[i]] == pytest.approx(close.iloc[i] / close.iloc[i - 1] - 1)
    assert f.signal.loc[d[i]] == bool(hot[i]) and not f.signal.iloc[:250].any()
    cut = st.frame(t, close, d[299])                             # a window ending earlier never sees later rows
    assert cut.index.max() == d[299] and cut.next.loc[d[299]] == pytest.approx(close.iloc[300] / close.iloc[299] - 1)


def test_a_real_edge_passes_and_no_edge_fails(monkeypatch):
    monkeypatch.setattr(st, "MIN_SIGNAL", {"train": 20, "holdout": 3})
    monkeypatch.setattr(st, "N_BOOT", 800)
    t, close, d, _ = make(n=900, edge=0.012)
    good = st.evaluate(st.frame(t, close, d[-1]), (d[250], d[-1]), "train")
    assert good["pass"] is True and good["verdict"] == "PASS" and good["diff"] > 0.008 and good["p"] < 0.05
    assert good["signal_days"] >= 20 and good["timer"]["total"] > 0
    t, close, d, _ = make(n=900, edge=0.0)
    none = st.evaluate(st.frame(t, close, d[-1]), (d[250], d[-1]), "train")
    assert none["pass"] is False and none["verdict"] == "FAIL"
    t, close, d, _ = make(n=900, edge=-0.012)
    bad = st.evaluate(st.frame(t, close, d[-1]), (d[250], d[-1]), "train")
    assert bad["pass"] is False and bad["p"] > 0.9


def test_too_few_signal_days_is_no_verdict_and_holdout_is_direction_only(monkeypatch):
    monkeypatch.setattr(st, "N_BOOT", 300)
    t, close, d, _ = make(n=400, edge=0.02)
    f = st.frame(t, close, d[-1])
    few = st.evaluate(f, (d[250], d[-1]), "train")               # about 30 signal days against the 150 required
    assert few["pass"] is False and few["verdict"].startswith("NO VERDICT") and few["p"] == 1.0
    h = st.evaluate(f, (d[250], d[-1]), "holdout")
    assert h["verdict"] == "CONSISTENT" and "pass" not in h
    t, close, d, _ = make(n=400, edge=-0.02)
    assert st.evaluate(st.frame(t, close, d[-1]), (d[250], d[-1]), "holdout")["verdict"] == "NOT CONSISTENT"


def test_boot_resamples_whole_months_and_fails_closed():
    d = days(60, "2021-01-04")
    f = pd.DataFrame({"next": [0.01] * 60, "signal": [True] * 30 + [False] * 30}, index=d)
    b = st.boot(f, st.diff)
    assert len(b) == st.N_BOOT and np.isneginf(b).any()          # a resample without both groups counts against


def test_main_refuses_a_second_training_run_and_a_holdout_without_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "result_train.json").write_text(json.dumps({"pass": False}))
    called = []
    monkeypatch.setattr(st.base, "load", lambda: called.append(1))
    assert st.main([]) == 2 and st.main(["--holdout"]) == 2 and called == []
    (tmp_path / "result_train.json").write_text(json.dumps({"pass": True}))
    (tmp_path / "result_holdout.json").write_text("{}")
    assert st.main(["--holdout"]) == 2 and called == []
