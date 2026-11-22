"""prereg_fade_price_test_2026-11-21: the flag, the outcome table and the run-once guard (no network, no real data)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_price_test_study as st  # noqa: E402

T = pd.Timestamp("2026-03-02 15:00:00", tz="UTC")


def bars(lows):
    return pd.DataFrame({"ts": [T + pd.Timedelta(minutes=i) for i in range(len(lows))], "low": lows})


def test_flag_today_uses_only_minutes_completed_before_the_entry():
    b = bars([9.5, 8.9, 9.6])                                    # the 15:01 minute trades 10% under the prior close 10.0
    assert st.price_test(b, T + pd.Timedelta(minutes=1), 10.0, 9.9, 10.0) is False     # 15:01 has not started
    assert st.price_test(b, T + pd.Timedelta(minutes=1, seconds=30), 10.0, 9.9, 10.0) is False   # not complete
    assert st.price_test(b, T + pd.Timedelta(minutes=2), 10.0, 9.9, 10.0) is True


def test_flag_carried_over_and_unknown():
    assert st.price_test(bars([9.9]), T, 10.0, 8.0, 9.0) is True       # yesterday fell 10% from the close before it
    assert st.price_test(bars([9.9]), T, 10.0, 8.5, 9.0) is False      # 8.5 > 8.1
    assert st.price_test(bars([9.9]), T, 10.0, 8.0, None) is False     # no close before: only today's part counts
    assert st.price_test(bars([1.0]), T + pd.Timedelta(hours=1), None, 8.0, 9.0) is None


@pytest.mark.parametrize("n_f, n_u, diff, unflagged, want", [
    (99, 500, (0.01, 0.05), (-0.01, 0.01), "TOO FEW"),
    (500, 99, (0.01, 0.05), (-0.01, 0.01), "TOO FEW"),
    (200, 500, (0.01, 0.05), (-0.01, 0.01), "SITS IN PRICE-TEST STOCKS"),
    (200, 500, (0.01, 0.05), (-0.03, -0.01), "SITS IN PRICE-TEST STOCKS"),
    (200, 500, (0.01, 0.05), (0.001, 0.02), "ALSO OUTSIDE"),
    (200, 500, (-0.01, 0.05), (0.001, 0.02), "NOT SHOWN"),
    (200, 500, (-0.05, -0.01), (0.001, 0.02), "REVERSED"),
])
def test_outcome_table(n_f, n_u, diff, unflagged, want):
    assert st.outcome(n_f, n_u, {"diff_ci": list(diff), "unflagged_ci": list(unflagged)}) == want


def test_bootstrap_resamples_whole_sessions_and_finds_a_plain_difference():
    rng = np.random.default_rng(1)
    ses = np.repeat([f"s{i:03d}" for i in range(200)], 4)
    flag = np.tile([True, True, False, False], 200)
    net = np.where(flag, 0.05, -0.02) + rng.normal(0, 0.01, len(ses))
    b = st.boot(ses, net, flag, np.random.default_rng(st.SEED))
    assert b["diff_ci"][0] > 0.06 and b["diff_ci"][1] < 0.08
    assert b["flagged_ci"][0] > 0.04 and b["unflagged_ci"][1] < -0.01


def test_flags_read_prior_day_and_stored_minutes_and_unknown_stays_unknown(tmp_path, monkeypatch):
    daily = {"AAA": pd.DataFrame({"d": ["2026-02-26", "2026-02-27"], "low": [9.0, 7.9], "close": [9.0, 8.5]}),
             "BBB": pd.DataFrame({"d": ["2026-02-26", "2026-02-27"], "low": [9.0, 8.9], "close": [9.0, 9.0]}),
             "NEW": pd.DataFrame(columns=["d", "low", "close"])}
    monkeypatch.setattr(st, "daily_bars", lambda client, sym, a, b, cache: daily[sym])

    class Store:
        def read_frame(self, sym, a, b):
            return pd.DataFrame({"ts": [T, T + pd.Timedelta(minutes=1)], "low": [9.0, 9.0], "segment": "rth"})
    ev = pd.DataFrame({"symbol": ["AAA", "BBB", "NEW"], "session": ["2026-03-02"] * 3,
                       "posted_at": ["2026-03-02T15:05:20+00:00"] * 3})
    got = st.flags(ev, Store(), None, tmp_path)
    assert got.tolist() == [True, False, None]                    # AAA: 7.9 <= 0.9 x 9.0 the day before; NEW: no bars


def test_single_run_guard(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "result.json").write_text("{}")
    assert st.main() == 1 and "already run" in capsys.readouterr().out
