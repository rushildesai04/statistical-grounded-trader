"""Study plumbing of prereg_money_score_2026-11-14 on synthetic records (never the real ones)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import money_score_study as ms  # noqa: E402


def records(informative: bool, seed=1, days=500, names=8):
    rng = np.random.default_rng(seed)
    sessions = [str(d.date()) for d in pd.bdate_range("2018-01-02", periods=days)]
    out = []
    for i in range(names):
        sigma = 0.01 * (1 + i)                                    # risk differs by name
        for s in sessions:
            pred = rng.normal(0, 0.004)
            label = (0.6 * pred if informative else 0.0) + rng.normal(0, sigma if informative else 0.01)
            out.append({"session": s, "symbol": f"S{i}", "pred": pred, "label": label, "d_rv21": sigma,
                        "position": 1 if pred > 0.003 else (-1 if pred < -0.003 else 0)})
    return pd.DataFrame(out)


def run(t, sides=(1,)):
    e = ms.scored(t)
    return ms.evaluate(e, ("2019-01-01", "2019-12-31"), "train", sides)


def test_an_informative_forecast_ranks_calls_and_the_study_reports_both_tests(monkeypatch):
    monkeypatch.setattr(ms, "N_BOOT", 1000)
    r = run(records(True))
    assert r["calls"] >= 300 and set(r) >= {"T1", "T2", "verdict"}
    assert r["T1"]["diff"] > 0 and r["T2"]["mean_diff"] > 0      # more forecast per unit of risk earns more
    assert r["T2"]["base"]["gross"] == pytest_approx(r["T2"]["score"]["gross"])   # equal dollars deployed


def test_a_forecast_with_no_information_does_not_pass(monkeypatch):
    monkeypatch.setattr(ms, "N_BOOT", 1000)
    r = run(records(False))
    assert r["verdict"] != "PASS"


def test_too_few_calls_is_no_verdict(monkeypatch):
    monkeypatch.setattr(ms, "N_BOOT", 200)
    t = records(True, days=320, names=1)
    r = run(t)
    assert r["calls"] < 300 and r["verdict"].startswith("NO VERDICT")


def test_bootstrap_p_fails_closed():
    s = np.array(["a", "b", "c", "d"] * 5)
    stat, lower, p = ms.boot_p(s, np.full(20, -0.01))
    assert stat < 0 and p == 1.0
    stat, lower, p = ms.boot_p(s, np.full(20, 0.01))
    assert stat > 0 and lower > 0 and p == 1 / (ms.N_BOOT + 1)


def pytest_approx(x):
    import pytest

    return pytest.approx(x, rel=1e-6)
