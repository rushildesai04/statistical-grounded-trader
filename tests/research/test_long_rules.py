"""Study plumbing of prereg_long_rules_2026-11-15 on synthetic data (the real record is read once, by the run)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_rules_study as st  # noqa: E402


def synth(n=400, seed=3):
    rng = np.random.default_rng(seed)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2021-01-04", periods=n)]
    rows = [{"session": d, "symbol": s, "label": float(rng.normal(0.001, 0.02)), "pred": 0.01, "position": 1,
             "d_rv21": 0.02} for d in days for s in ("AAA", "BBB", "CCC")]
    return pd.DataFrame(rows), days


def test_skipping_losing_earnings_calls_passes_and_winning_ones_fail():
    t, days = synth()
    marked = days[10::8]                                                   # 49 holds with a release, all AAA
    earn = {"AAA": pd.Series(pd.to_datetime([f"{d} 16:10:00" for d in marked]).tz_localize("America/New_York"))}
    hit = (t.symbol == "AAA") & t.session.isin(marked)
    bad = t.copy()
    bad.loc[hit, "label"] = -0.05
    r = st.evaluate(bad, st.books(bad, earn), (days[0], days[-1]), "train")
    assert r["E"]["skipped"]["n"] == len(marked) and r["E"]["mean_diff"] > 0 and r["E"]["verdict"] == "PASS"
    assert abs(r["E"]["skipped"]["mean"] - (-0.05 - 0.0004)) < 1e-12              # net of the 4 bp cost
    assert r["E"]["kept"]["n"] == r["calls"] - len(marked)
    good = t.copy()
    good.loc[hit, "label"] = 0.05
    r = st.evaluate(good, st.books(good, earn), (days[0], days[-1]), "train")
    assert r["E"]["mean_diff"] < 0 and r["E"]["p"] > 0.5 and r["E"]["verdict"] == "FAIL"


def test_too_few_skipped_calls_is_no_verdict():
    t, days = synth()
    marked = days[10::20][:20]                                             # 20 losing holds: clear, but under 30
    earn = {"AAA": pd.Series(pd.to_datetime([f"{d} 16:10:00" for d in marked]).tz_localize("America/New_York"))}
    t.loc[(t.symbol == "AAA") & t.session.isin(marked), "label"] = -0.05
    r = st.evaluate(t, st.books(t, earn), (days[0], days[-1]), "train")
    assert r["E"]["skipped"]["n"] == 20 and r["E"]["mean_diff"] > 0 and r["E"]["p"] < st.ALPHA
    assert r["E"]["pass"] is False and r["E"]["verdict"].startswith("NO VERDICT")


def test_a_skipped_call_leaves_the_days_other_weights_alone():
    rng = np.random.default_rng(5)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2021-01-04", periods=30)]
    t = pd.DataFrame([{"session": d, "symbol": s, "label": float(rng.normal(0, 0.01)), "pred": 0.01, "position": 1,
                       "d_rv21": 0.02} for d in days for s in "ABCDE"])          # 5 x 25% -> each scaled to 20%
    earn = {"A": pd.Series(pd.to_datetime([f"{days[7]} 16:10:00"]).tz_localize("America/New_York"))}
    b = st.books(t, earn)
    assert abs(b["base"].gross[days[7]] - 1.0) < 1e-12 and abs(b["E"].gross[days[7]] - 0.8) < 1e-12
    assert abs(b["E"].gross[days[8]] - 1.0) < 1e-12


def test_cap_compares_at_the_same_average_money():
    t, days = synth()
    r = st.evaluate(t, st.books(t, {}), (days[0], days[-1]), "train")
    v = r["V"]
    assert v["binds"] > 100 and 0 < v["avg_cut"] < 1 and v["k"] > 1
    assert abs(v["book_same_money"]["gross"] - r["base"]["gross"]) < 1e-9 and v["book_raw"]["gross"] < r["base"]["gross"]


def test_bootstrap_p_is_fail_closed():
    assert st.boot(np.array([]))[2] == 1.0
    assert st.boot(np.full(50, -0.01))[2] == 1.0 and st.boot(np.full(50, 0.01))[2] == 1 / (st.N_BOOT + 1)
    assert st.boot(np.zeros(50))[2] == 1.0                                 # no difference counts against


def test_the_held_out_slice_stays_closed_without_a_training_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(st, "OUT", tmp_path)
    monkeypatch.setattr(st, "load", lambda: (_ for _ in ()).throw(AssertionError("the record must not be read")))
    assert st.main(["--holdout", "E"]) == 2                                # no training result at all
    (tmp_path / "result_train.json").write_text(json.dumps({"E": {"pass": False}, "V": {"pass": True}}))
    assert st.main(["--holdout", "E"]) == 2 and "stays closed" in capsys.readouterr().out
