"""Plumbing of prereg_after_day_borrow_forward_2026-11-17 on synthetic snapshots and bars (the decision is forward)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import after_day_borrow_study as st  # noqa: E402


def snap_file(root: Path, day: str, hhmm_utc: str, rows):
    d = root / day.replace("-", "")
    d.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["symbol", "fee", "avail"]).assign(rebate=0.0, snap="x").to_parquet(d / f"{hhmm_utc}.parquet")


def daily(closes, start="2026-11-16"):
    idx = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=len(closes))]
    return pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes}, index=idx)


def test_closing_snapshot_is_the_last_list_at_or_before_the_close(tmp_path):
    snap_file(tmp_path, "2026-11-17", "1700", [("AAA", 5.0, 100)])            # 12:00 ET: more than 3 h before the close
    assert st.closing_snapshot("2026-11-17", tmp_path) is None
    snap_file(tmp_path, "2026-11-17", "2045", [("AAA", 50.0, 9000)])          # 15:45 ET
    snap_file(tmp_path, "2026-11-17", "2100", [("AAA", 60.0, 8000)])          # 16:00 ET: the closing list
    snap_file(tmp_path, "2026-11-17", "2114", [("AAA", 70.0, 10)])            # 16:14 ET: after the close, not used
    s = st.closing_snapshot("2026-11-17", tmp_path)
    assert (s.loc["AAA"].fee, s.loc["AAA"].avail) == (60.0, 8000)
    assert st.closing_snapshot("2026-11-18", tmp_path) is None                # no list stored that day
    snap_file(tmp_path, "2026-07-01", "2000", [("AAA", 1.0, 5)])              # summer: 16:00 ET is 20:00 UTC
    assert st.closing_snapshot("2026-07-01", tmp_path).loc["AAA"].avail == 5


def test_lendable_needs_the_shares_of_a_2500_dollar_position():
    s = pd.DataFrame({"fee": [40.0, np.nan], "avail": [1000, 5000]}, index=["AAA", "BBB"])
    assert st.lendable(s, "AAA", 2.50) == (True, 40.0)                        # needs 1,000 shares: exactly enough
    assert st.lendable(s, "AAA", 2.49) == (False, 40.0)                       # needs 1,005
    assert st.lendable(s, "BBB", 1.0) == (True, None) and st.lendable(s, "ZZZ", 5.0) == (False, None)
    assert st.lendable(None, "AAA", 5.0) == (None, None)


def test_trade_covers_at_the_horizon_or_at_the_first_close_without_the_shares():
    d = daily([10.0, 9.0, 8.0, 8.5, 7.0, 6.0, 6.0, 6.0])                      # Mon 11-16 .. Wed 11-25
    full = pd.DataFrame({"fee": [36.0], "avail": [100000]}, index=["AAA"])
    gone = pd.DataFrame({"fee": [90.0], "avail": [10]}, index=["AAA"])
    always = lambda day: full  # noqa: E731
    s1 = st.trade(d, always, "AAA", "2026-11-16", 0.01, 1)
    assert s1["borrowable"] is True and s1["days"] == 1 and s1["recalled"] is False
    assert s1["net"] == pytest.approx(0.10 - 0.01 - 36.0 / 100 / 360 * 1)
    s5 = st.trade(d, always, "AAA", "2026-11-16", 0.01, 5)
    assert s5["cover_day"] == "2026-11-23" and s5["days"] == 7                # five sessions, seven calendar days
    assert s5["net"] == pytest.approx(0.40 - 0.01 - 36.0 / 100 / 360 * 7)
    recall = lambda day: gone if day == "2026-11-18" else full  # noqa: E731     # shares gone at Wednesday's close
    r5 = st.trade(d, recall, "AAA", "2026-11-16", 0.01, 5)
    assert r5["recalled"] is True and r5["cover_day"] == "2026-11-18" and r5["days"] == 2
    assert r5["net"] == pytest.approx(0.20 - 0.01 - 90.0 / 100 / 360 * 2)     # the highest fee seen is charged
    nob = st.trade(d, lambda day: gone, "AAA", "2026-11-16", 0.01, 1)
    assert nob["borrowable"] is False
    assert st.trade(d, always, "AAA", "2026-11-19", 0.01, 5) is None           # five sessions ahead are not there
    assert st.trade(d, always, "AAA", "2025-01-02", 0.01, 1) is None


def test_count_reports_eligibility_and_no_outcome():
    d = daily([10.0] * 12)
    full = pd.DataFrame({"fee": [5.0], "avail": [100000]}, index=["AAA"])
    ev = pd.DataFrame({"symbol": ["AAA", "BBB", "AAA", "CCC"], "session": ["2026-11-16", "2026-11-16", "2026-11-27", "2026-11-17"],
                       "cost_rt": 0.01})
    n = st.count(ev, lambda s: d if s in ("AAA", "BBB") else None, lambda day: full if day == "2026-11-16" else None)
    assert n == {"alert_days": 4, "with_bars_and_5_sessions": 2, "borrow_known": 2, "borrowable": 1}
    assert not any(k in json.dumps(n) for k in ("net", "ret", "mean"))


def rows(n=400, edge=0.03, seed=0):
    rng = np.random.default_rng(seed)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2026-11-04", periods=n // 5)]
    one = pd.DataFrame({"symbol": [f"S{i}" for i in range(n)], "session": [days[i % len(days)] for i in range(n)],
                        "borrowable": rng.random(n) < 0.85, "net": rng.normal(edge, 0.10, n),
                        "recalled": rng.random(n) < 0.1, "carry": 0.002})
    return pd.concat([one.assign(horizon="S1"), one.assign(horizon="S5")], ignore_index=True)


def test_decision_needs_300_borrowable_days_and_a_positive_lower_bound(monkeypatch):
    monkeypatch.setattr(st, "N_BOOT", 400)
    good = st.decide(rows(edge=0.03))
    assert good["S1"]["pass"] is True and good["S1"]["borrowable_events"] >= 300 and good["S1"]["lower"] > 0
    assert good["S1"]["not_borrowable_mean_net"] is not None and 0 < good["S1"]["borrowable_share"] < 1
    assert st.decide(rows(edge=0.0))["S5"]["pass"] is False
    assert st.decide(rows(n=200, edge=0.05))["S1"]["pass"] is False            # under 300 borrowable days


def test_the_decision_run_is_refused_before_the_count_and_after_a_result(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st, "OUT", tmp_path)
    monkeypatch.setattr(st, "forward_events", lambda path=None: pd.DataFrame({"symbol": [], "session": [], "cost_rt": []}))
    monkeypatch.setattr(st, "daily_loader", lambda today: (lambda s: None))
    assert st.main([]) == 3 and "no outcome is computed" in capsys.readouterr().out   # 0 borrowable days: refused
    assert not (tmp_path / "result.json").exists()
    assert st.main(["--count"]) == 0
    (tmp_path / "result.json").write_text("{}")
    assert st.main([]) == 2
