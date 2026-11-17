"""Plumbing of prereg_timing_maps_2026-11-17 on synthetic bars (the real map is made once, by the run)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_timing_map as tm  # noqa: E402

T0 = pd.Timestamp("2026-03-03T15:00:00Z")                        # 10:00 ET


def bars(closes, skip=()):
    n = len(closes)
    opens = [closes[0]] + list(closes[:-1])
    f = pd.DataFrame({"ts": [T0 + pd.Timedelta(minutes=i) for i in range(n)], "open": opens, "close": closes,
                      "high": [max(o, c) for o, c in zip(opens, closes)],
                      "low": [min(o, c) for o, c in zip(opens, closes)], "volume": [1000.0] * n})
    return f.drop(index=list(skip)).reset_index(drop=True)


def test_stall_moment_is_the_first_bar_with_a_stale_low_while_in_profit():
    # short 10.00; falls to 9.00 at minute 3, then creeps up 1 cent a minute, still under the entry
    c = [10.0, 9.6, 9.3, 9.0] + [9.0 + 0.01 * i for i in range(1, 80)]
    got = {x["n"]: x for x in tm.stall_moments(bars(c), T0, 10.0)}
    assert sorted(got) == [5, 10, 15, 20]
    five = got[5]
    assert five["minute"] == 9                                    # low at bar 3, stale 5 min at bar 8 (the 9th bar)
    assert five["profit"] == pytest.approx((10.0 - 9.05) / 10.0)
    assert five["cover"] == pytest.approx((10.0 - 9.05) / 10.0)   # next bar opens at this bar's close
    assert got[20]["minute"] == 24 and got[20]["hold"] == five["hold"] and five["how"] == "cap"
    assert five["hold"] == pytest.approx((10.0 - c[59]) / 10.0)   # the champion's own 60-minute result


def test_a_new_low_resets_the_clock_and_a_losing_short_has_no_moment():
    c = [10.0, 9.5] + [9.5] * 4 + [9.0] + [9.0] * 70             # second low at minute 6: stale 5 only at minute 11
    got = {x["n"]: x for x in tm.stall_moments(bars(c), T0, 10.0)}
    assert got[5]["minute"] == 12
    up = [10.0] + [10.0 + 0.01 * i for i in range(1, 80)]         # never in profit
    assert tm.stall_moments(bars(up), T0, 10.0) == []
    stopped = [10.0, 9.0] + [9.0] * 3 + [12.5] * 60               # stopped at minute 5: only moments before it count
    assert [x["n"] for x in tm.stall_moments(bars(stopped), T0, 10.0)] == []


def test_nothing_is_decided_on_or_after_the_champions_exit_bar():
    c = [10.0] + [9.0] * 100
    got = tm.stall_moments(bars(c), T0, 10.0)
    assert all(x["minute"] < 60 for x in got) and len(got) == 4


def test_long_entries_buy_the_open_at_each_time_and_hold_thirty_minutes():
    c = [10.0 + 0.01 * i for i in range(200)]
    got = {x["k"]: x for x in tm.long_entries(bars(c), T0, 10.0)}
    assert sorted(got) == list(tm.ENTRY_K)
    assert got[0]["rel"] == pytest.approx(0.0) and got[0]["gross"] == pytest.approx(c[29] / 10.0 - 1)
    assert got[30]["rel"] == pytest.approx(c[29] / 10.0 - 1) and got[30]["gross"] == pytest.approx(c[59] / c[29] - 1)
    halted = bars(c, skip=range(40, 60))                          # no print for 20 minutes around entry + 45
    assert 45 not in {x["k"] for x in tm.long_entries(halted, T0, 10.0)}
    late = bars(c).assign(ts=lambda f: f.ts + pd.Timedelta(hours=5, minutes=20))    # 15:20 ET start
    ks = {x["k"] for x in tm.long_entries(late, late.ts.iloc[0], 10.0)}
    assert ks == {0, 5, 10}                                       # no entry after 15:30 ET


def cells(n=400, cont=-0.02, seed=0):
    rng = np.random.default_rng(seed)
    days = [f"2025-{1 + i % 12:02d}-{1 + i % 27:02d}" for i in range(n)]
    cover = rng.normal(0.06, 0.02, n)
    return pd.DataFrame({"symbol": [f"S{i}" for i in range(n)], "session": days, "cost": 0.009, "n": 10,
                         "profit": 0.07, "cover": cover, "hold": cover + cont + rng.normal(0, 0.03, n), "how": "cap",
                         "minute": 20})


def test_a_cell_is_a_finding_only_when_leaving_clearly_beats_holding(monkeypatch):
    monkeypatch.setattr(tm, "N_BOOT", 500)
    (bad,) = tm.map_a(cells(cont=-0.02))                          # holding gives 2 points back: a finding
    assert bad["finding"] is True and bad["continuation"] < 0 and bad["upper"] < 0 and bad["profit_then"] == "5 to 10%"
    (good,) = tm.map_a(cells(cont=+0.02))
    assert good["finding"] is False and good["continuation"] > 0
    (few,) = tm.map_a(cells(n=100, cont=-0.05))                   # under 150 events: never a finding
    assert few["finding"] is False
    (noise,) = tm.map_a(cells(cont=-0.001))
    assert noise["finding"] is False


def test_long_cells_need_a_positive_lower_bound_after_costs(monkeypatch):
    monkeypatch.setattr(tm, "N_BOOT", 500)
    n = 600
    days = [f"2025-{1 + i % 12:02d}-{1 + i % 27:02d}" for i in range(n)]
    rng = np.random.default_rng(1)
    b = pd.DataFrame({"symbol": "S", "session": days, "cost": 0.009, "k": 30, "rel": -0.05,
                      "gross": rng.normal(0.03, 0.03, n)})
    rows = tm.map_b(b)
    assert rows[0]["finding"] is True and rows[0]["price_vs_alert"] == "down less than 10%" and rows[0]["net"] > 0.015
    assert rows[-1]["all_prices"][0]["events"] == n
    b["gross"] = rng.normal(0.005, 0.03, n)                       # +0.5% before costs: a loss after them
    assert tm.map_b(b)[0]["finding"] is False


def test_stall_as_a_whole_rule_falls_back_to_the_champion():
    a = pd.DataFrame({"symbol": ["A"], "session": ["2025-01-02"], "cost": 0.01, "n": 5, "profit": 0.1, "cover": 0.08,
                      "hold": 0.02, "how": "cap", "minute": 9})
    base = pd.DataFrame({"symbol": ["A", "B"], "session": ["2025-01-02"] * 2, "cost": 0.01, "hold": [0.02, 0.04]})
    r = {x["rule"]: x for x in tm.rules_a(a, base)}
    assert r["STALL-5"]["mean"] == pytest.approx((0.08 + 0.04) / 2 - 0.015) and r["STALL-5"]["ended_by_rule"] == 0.5
    assert r["STALL-10"]["mean"] == pytest.approx(r["STALL-10"]["champion"]) == pytest.approx(0.03 - 0.015)


def test_main_refuses_a_second_run(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "OUT", tmp_path)
    (tmp_path / "result_train.json").write_text("{}")
    called = []
    monkeypatch.setattr(tm, "events", lambda split: called.append(split))
    assert tm.main() == 2 and called == []
