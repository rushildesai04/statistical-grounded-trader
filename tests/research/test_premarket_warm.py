"""Plumbing of prereg_fade_premarket_warm_2026-11-17 on synthetic data (the real run is made once)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_premarket_warm_study as st  # noqa: E402


def test_warm_is_an_alert_before_the_open_on_the_same_eastern_day():
    a = pd.DataFrame({"symbol": ["GP", "GP", "MPU", "XX", "YY"],
                      "posted_at": ["2026-11-17T13:05:00+00:00",      # 08:05 ET: pre-market
                                    "2026-11-17T14:30:04+00:00",      # 09:30:04 ET: regular hours
                                    "2026-11-17T15:02:02+00:00",      # 10:02 ET
                                    "2026-11-17T14:29:59+00:00",      # 09:29:59 ET: still before the open
                                    "2026-07-01T13:29:00+00:00"]})    # 09:29 ET in summer time
    assert st.warm_pairs(a) == {("GP", "2026-11-17"), ("XX", "2026-11-17"), ("YY", "2026-07-01")}
    late = pd.DataFrame({"symbol": ["ZZ"], "posted_at": ["2026-11-17T02:30:00+00:00"]})   # 21:30 ET the day BEFORE
    assert st.warm_pairs(late) == set()


def events(n=400, warm_edge=-0.03, seed=0):
    rng = np.random.default_rng(seed)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-02", periods=n // 4)]
    warm = rng.random(n) < 0.4
    return pd.DataFrame({"symbol": [f"S{i}" for i in range(n)], "session": [days[i % len(days)] for i in range(n)],
                         "posted_at": "2025-01-02 15:00:00+00:00", "news_own": 0, "split": "dev", "warm": warm,
                         "net": rng.normal(0.02, 0.05, n) + np.where(warm, warm_edge, 0.0)})


def test_pass_needs_a_worse_warm_group_that_loses_money():
    worse = st.stats(events(warm_edge=-0.05))
    assert st.train_pass(worse) and worse["diff"] > 0.03 and worse["p"] < 0.05 and worse["skipped_total"] < 0
    same = st.stats(events(warm_edge=0.0))
    assert not st.train_pass(same)
    better = st.stats(events(warm_edge=+0.05))
    assert not st.train_pass(better) and better["p_opposite"] < 0.05           # the opposite reading shows up here
    profitable = st.stats(events(warm_edge=-0.012))                             # worse, but skipping it gives up money
    assert profitable["skipped_total"] > 0 and not st.train_pass(profitable)
    few = st.stats(events(n=120, warm_edge=-0.05))
    assert few["n_skipped"] < st.MIN_TRAIN and not st.train_pass(few)


def test_rule_set_keeps_no_news_fresh_low_beta_events():
    d = pd.DataFrame({"symbol": ["A", "B", "C", "D", "D"], "session": ["2025-03-03"] * 4 + ["2025-03-05"],
                      "news_own": [0, 1, 0, 0, 0], "net": 0.01, "split": "dev"})
    beta = pd.DataFrame({"symbol": ["A", "C"], "session": ["2025-03-03"] * 2, "beta": [1.0, 4.2]})
    kept = st.rule_set(d, beta)
    assert sorted(zip(kept.symbol, kept.session)) == [("A", "2025-03-03"), ("D", "2025-03-03")]   # B news, C beta, D repeat


def test_main_refuses_a_second_run(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "result.json").write_text("{}")
    called = []
    monkeypatch.setattr(st, "premarket_days", lambda db=st.DB: called.append(1))
    assert st.main() == 2 and called == []
