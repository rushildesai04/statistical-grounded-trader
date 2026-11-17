"""Study plumbing of prereg_fresh_names_2026-11-17 on synthetic data (the real record is read once, by the run)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fresh_names_study as st  # noqa: E402
from sgt.megacap.daily import _today  # noqa: E402
from sgt.megacap.features import to_grid  # noqa: E402


def session_m15(day="2021-03-02", base=100.0, bump=None):
    """26 quarter-hour bars of one regular session; bar i opens at base + i and closes at base + i + 0.5."""
    ts = pd.date_range(f"{day} 09:30", periods=26, freq="15min", tz=st.ET).tz_convert("UTC")
    o = base + np.arange(26.0)
    df = pd.DataFrame({"ts": ts, "open": o, "high": o + 0.8, "low": o - 0.3, "close": o + 0.5, "volume": 1000.0})
    for i, vals in (bump or {}).items():
        for k, v in vals.items():
            df.loc[i, k] = v
    return df


def today(m15):
    grid = to_grid(st.expand(m15))
    (a,) = grid.values()
    return _today(a)


def test_expand_lays_a_bar_as_its_open_then_the_full_bar_in_its_last_minute():
    e = st.expand(session_m15())
    assert len(e) == 26 * 15
    first = e.iloc[:15]
    assert first.close.iloc[:14].eq(100.0).all() and first.close.iloc[14] == 100.5
    assert first.high.iloc[:14].eq(100.0).all() and first.high.iloc[14] == 100.8
    assert first.low.iloc[14] == 99.7 and first.volume.iloc[:14].eq(0).all() and first.volume.iloc[14] == 1000.0
    assert str(e.ts.iloc[0].tz_convert(st.ET))[11:16] == "09:30" and str(e.ts.iloc[14].tz_convert(st.ET))[11:16] == "09:44"


def test_the_cutoff_reads_the_1545_open_and_nothing_printed_after_it():
    intr, r60, rng, k = today(session_m15())
    # bar 25 starts 15:45 (open 125), bar 21 starts 14:45 (open 121); bars 0..24 have ended by 15:45
    assert k == 379
    assert intr == pytest.approx(np.log(125.0 / 100.0))
    assert r60 == pytest.approx(np.log(125.0 / 121.0))
    assert rng == pytest.approx((125.0 - (100 - 0.3)) / 100.0)          # top = the 15:45 open, a print by the cutoff
    # what the 15:45 bar does AFTER its open is the future: high, low, close and volume there change nothing
    later = today(session_m15(bump={25: {"high": 500.0, "low": 1.0, "close": 300.0, "volume": 9e9}}))
    assert later == pytest.approx((intr, r60, rng, k))
    # its open is the 15:45 print and is read
    assert today(session_m15(bump={25: {"open": 130.0}}))[0] == pytest.approx(np.log(130.0 / 100.0))


def test_agg15_of_one_minute_bars_matches_the_quarter_hours():
    ts = pd.date_range("2021-03-02 09:30", periods=390, freq="1min", tz=st.ET).tz_convert("UTC")
    c = 50 + np.arange(390.0) / 10
    one = pd.DataFrame({"ts": ts, "open": c - 0.05, "high": c + 0.2, "low": c - 0.2, "close": c, "volume": 10.0})
    pre = one.iloc[:1].assign(ts=one.ts.iloc[0] - pd.Timedelta(minutes=5))          # pre-market print: ignored
    a = st.agg15(pd.concat([pre, one]))
    assert len(a) == 26
    assert a.open.iloc[0] == pytest.approx(c[0] - 0.05) and a.close.iloc[0] == pytest.approx(c[14])
    assert a.high.iloc[1] == pytest.approx(c[29] + 0.2) and a.low.iloc[1] == pytest.approx(c[15] - 0.2)
    assert a.volume.eq(150.0).all()


def test_parse_bars_refuses_malformed_rows_and_regular_hours_drops_the_rest():
    ok = [{"t": "2021-03-02T14:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
          {"t": "2021-03-02T09:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},      # 04:00 ET
          {"t": "2021-03-02T21:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10}]      # 16:00 ET
    df = st.regular_hours(st.parse_bars(ok))
    assert len(df) == 1 and df.close.iloc[0] == 1.5
    for bad in ({"o": 0}, {"h": 0.1}, {"c": float("nan")}, {"v": -1}):
        with pytest.raises(ValueError):
            st.parse_bars([{**ok[0], **bad}])
    with pytest.raises(KeyError):
        st.parse_bars([{"t": "2021-03-02T14:30:00Z", "o": 1}])
    with pytest.raises(ValueError):
        st.parse_bars("not a list")


def test_pick_drops_funds_seen_names_second_listings_and_names_that_ended_early():
    r = pd.DataFrame({"symbol": ["SPY", "AAPL", "BABA", "GOOG", "CELG", "BA", "XLF", "DIS"],
                      "dv": [9, 8, 7, 6, 5, 4, 3, 2.0],
                      "last": ["2026-10-30", "2026-10-30", "2026-10-30", "2026-10-30", "2019-11-20", "2026-10-30",
                               "2026-10-30", "2026-10-30"]})
    assert st.pick(r, n=2) == ["BABA", "BA"]
    assert st.pick(r, n=10) == ["BABA", "BA", "DIS"]


def frame(calls, days=3, names=("A", "B", "C", "D")):
    """Every name has a row each day with return 1%; ``calls`` = {(day index, name): return} are the long calls."""
    d = [f"2021-01-0{i + 4}" for i in range(days)]
    rows = [{"session": s, "symbol": n, "label": calls.get((i, n), 0.01), "pred": 0.0,
             "position": int((i, n) in calls), "d_rv21": 0.02} for i, s in enumerate(d) for n in names]
    return pd.DataFrame(rows)


def test_ledger_divides_by_every_name_and_charges_each_call():
    t = frame({(0, "A"): 0.05, (0, "B"): -0.01, (2, "C"): 0.03})
    led = st.ledger(t)
    assert led.tolist() == pytest.approx([(0.05 - st.COST + -0.01 - st.COST) / 4, 0.0, (0.03 - st.COST) / 4])
    assert st.ledger(t, 2 * st.COST).iloc[2] == pytest.approx((0.03 - 2 * st.COST) / 4)


def test_excess_compares_the_calls_with_all_names_on_days_with_a_call():
    t = frame({(0, "A"): 0.05, (0, "B"): -0.01, (2, "C"): 0.03})
    e = st.excess(t)
    assert list(e.index) == ["2021-01-04", "2021-01-06"]                       # the day without a call is not scored
    assert e.iloc[0] == pytest.approx(0.02 - (0.05 - 0.01 + 0.01 + 0.01) / 4)
    assert e.iloc[1] == pytest.approx(0.03 - (0.03 + 0.03) / 4)


def test_boot_is_fail_closed():
    assert st.boot(np.array([]))[2] == 1.0
    m, lo, p = st.boot(np.full(50, -0.01))
    assert m == pytest.approx(-0.01) and p == 1.0
    m, lo, p = st.boot(np.full(50, 0.01))
    assert lo > 0 and p == pytest.approx(1 / (st.N_BOOT + 1))


def test_a_small_record_is_no_verdict_and_fails(monkeypatch):
    t = frame({(i, "A"): 0.05 for i in range(3)})
    res = st.evaluate(t, ("2021-01-01", "2021-12-31"), "train", [])
    assert res["pass"] is False and res["verdict"].startswith("NO VERDICT")
    monkeypatch.setattr(st, "MIN_SCORED", 4)
    monkeypatch.setattr(st, "MIN_CALLS", 3)
    days = 40
    good = st.evaluate(frame({(i, "A"): 0.05 for i in range(days)}, days=days, names=("A", "B", "C", "D")) if days < 6 else
                       wide({(i, "A"): 0.05 for i in range(days)}, days), ("2021-01-01", "2021-12-31"), "train", [])
    assert good["pass"] is True and good["verdict"] == "PASS"
    bad = st.evaluate(wide({(i, "A"): -0.05 for i in range(days)}, days), ("2021-01-01", "2021-12-31"), "train", [])
    assert bad["pass"] is False and bad["verdict"] == "FAIL"
    # skill without profit is not a pass: calls beat the other names but still lose money
    both_down = wide({(i, "A"): -0.001 for i in range(days)}, days, other=-0.02)
    r = st.evaluate(both_down, ("2021-01-01", "2021-12-31"), "train", [])
    assert r["D2"]["mean"] > 0 and r["D1"]["mean"] < 0 and r["pass"] is False


def wide(calls, days, other=0.01, names=("A", "B", "C", "D")):
    d = [x.strftime("%Y-%m-%d") for x in pd.bdate_range("2021-01-04", periods=days)]
    rows = [{"session": s, "symbol": n, "label": calls.get((i, n), other), "pred": 0.0,
             "position": int((i, n) in calls), "d_rv21": 0.02} for i, s in enumerate(d) for n in names]
    return pd.DataFrame(rows)


def test_holdout_reports_direction_only():
    r = st.evaluate(wide({(i, "A"): 0.05 for i in range(10)}, 10), ("2021-01-01", "2021-12-31"), "holdout", [])
    assert r["verdict"] == "CONSISTENT" and "pass" not in r
    r = st.evaluate(wide({(i, "A"): -0.05 for i in range(10)}, 10), ("2021-01-01", "2021-12-31"), "holdout", [])
    assert r["verdict"] == "NOT CONSISTENT"


def test_main_refuses_a_second_training_run_and_a_holdout_without_a_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "universe.json").write_text(json.dumps({"names": ["BA"]}))
    (tmp_path / "result_train.json").write_text(json.dumps({"pass": False}))
    called = []
    monkeypatch.setattr(st, "record", lambda *a, **k: called.append(a) or (pd.DataFrame(), []))
    assert st.main([]) == 2 and st.main(["--holdout"]) == 2 and st.main(["--universe"]) == 2
    assert called == []
    (tmp_path / "result_train.json").write_text(json.dumps({"pass": True}))
    (tmp_path / "result_holdout.json").write_text("{}")
    assert st.main(["--holdout"]) == 2 and called == []
