"""Plumbing of prereg_ema_instant_volume_2026-11-18 on synthetic prints (the study itself runs once on training)."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ema_instant_volume_study as st  # noqa: E402

T0 = pd.Timestamp("2026-01-05 15:00", tz="UTC")
NS = 1_000_000_000


def prints(rows):
    """(seconds into the minute, price, size) -> the cache's (ns, price, size) rows."""
    return np.array([(T0.value + s * NS, p, v) for s, p, v in rows], float).reshape(-1, 3)


def event(fake=False, **kw):
    d = dict(symbol="AAA", session="2026-01-05", ts=T0, minute=600, fake=fake, pstar=10.0, p_NEXT=10.30, p_PLUS1=10.40,
             cost_EXACT=0.007, ok_5=True, ok_15=True, ok_30=True, m_5=10.50, m_15=10.60, m_30=10.70)
    d.update(kw)
    return SimpleNamespace(**d)


def test_trigger_needs_price_above_the_cross_and_volume_already_there():
    tr = prints([(1, 10.05, 100), (5, 9.95, 2000), (9, 9.99, 1500), (20, 10.02, 50), (30, 10.10, 10)])
    assert st.trigger(tr, 10.0, 3000) == 3             # 10.05 came before the volume; 9.95/9.99 are below the cross
    assert st.trigger(tr, 10.0, 100) == 0
    assert st.trigger(tr, 10.0, 99_999) is None and st.trigger(tr, 10.2, 100) is None
    assert st.trigger(tr, 10.0, float("nan")) is None and st.trigger(np.empty((0, 3)), 10.0, 1) is None


def test_fast_buys_the_first_print_a_second_later():
    tr = prints([(10, 10.05, 4000), (10.4, 10.06, 10), (11.2, 10.12, 10), (40, 10.5, 10)])
    assert st.fast_fill(tr, tr[0, 0]) == pytest.approx(10.12)
    assert st.fast_fill(prints([(10, 10.05, 4000)]), T0.value + 10 * NS) is None


def test_fast_can_fill_in_the_next_minute_but_not_later_than_a_minute():
    this = prints([(59.5, 10.05, 4000)])
    row = st.score(event(), 1000.0, this, lambda: prints([(61, 10.20, 10)]))
    assert row["p_FAST"] == pytest.approx(10.20) and row["trig_s"] == pytest.approx(59.5)
    late = st.score(event(), 1000.0, prints([(2, 10.05, 4000)]), lambda: prints([(63, 10.20, 10)]))
    assert np.isnan(late["p_FAST"]) and np.isnan(late["g_HOLD_5"]) and np.isnan(late["g_NEXT_5"])


def test_no_trigger_no_event():
    assert st.score(event(), 1000.0, prints([(5, 10.05, 100), (9, 9.9, 5000)]), lambda: None) is None


def test_hold_and_cut_differ_only_on_a_failed_cross():
    tr = prints([(10, 10.05, 4000), (12, 10.10, 10)])
    held = st.score(event(), 1000.0, tr, lambda: None)
    assert held["g_HOLD_15"] == pytest.approx(10.60 / 10.10 - 1) == held["g_CUT_15"]
    assert held["g_NEXT_15"] == pytest.approx(10.60 / 10.30 - 1)
    failed = st.score(event(fake=True, p_NEXT=9.90), 1000.0, tr, lambda: None)
    assert failed["g_HOLD_15"] == pytest.approx(10.60 / 10.10 - 1)
    assert failed["g_CUT_5"] == failed["g_CUT_30"] == pytest.approx(9.90 / 10.10 - 1)


def test_a_mark_dropped_by_the_first_study_stays_dropped():
    row = st.score(event(ok_5=False), 1000.0, prints([(10, 10.05, 4000), (12, 10.10, 10)]), lambda: None)
    assert np.isnan(row["g_HOLD_5"]) and not np.isnan(row["g_HOLD_15"])


def table(gross, sessions):
    n = len(gross)
    d = {"symbol": "AAA", "session": sessions, "minute": 600, "fake": False, "trig_s": 20.0, "p_FAST": 5.0, "p_NEXT": 5.05,
         "p_PLUS1": 5.06, "cost": 0.007}
    for name in ("HOLD", "CUT", "NEXT", "PLUS1"):
        for m in st.MARKS:
            d[f"g_{name}_{m}"] = gross
    return pd.DataFrame(d, index=range(n))


def test_gate_needs_a_net_lower_bound_above_zero():
    rng = np.random.default_rng(3)
    sessions = [f"2026-01-{d:02d}" for d in rng.integers(1, 29, 500)]
    good = st.evaluate(table(rng.normal(0.02, 0.01, 500), sessions), 900)
    assert good["gate"]["pass"] and good["triggered"] == 500 and good["delay"]["NEXT"]["mean"] == pytest.approx(0.01)
    thin = st.evaluate(table(rng.normal(0.007, 0.01, 500), sessions), 900)       # gross equal to the cost: net 0
    assert not thin["gate"]["pass"]


def test_gate_fails_closed_without_any_scored_trade():
    t = table(np.full(20, np.nan), ["2026-01-05"] * 20)
    assert st.evaluate(t, 50)["gate"]["pass"] is False


def test_a_second_run_is_refused(tmp_path, capsys):
    (tmp_path / "result_train.json").write_text("{}")
    assert st.main(["--out", str(tmp_path)]) == 2
    assert "not repeated" in capsys.readouterr().out
