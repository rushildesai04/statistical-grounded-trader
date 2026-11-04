from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fast.test_fade import T10, Outbox, alert, code_in, db, rows  # noqa: E402,F401 - db is a fixture
from sgt.fast import fade, realism  # noqa: E402
from sgt.ops import approvals as ap  # noqa: E402
from sgt.ops.sms_style import bold  # noqa: E402


def test_realism_measures_gap_latency_mode_and_waiting_cost(db):  # noqa: F811
    assert realism.lines(db) == [] and realism.summary(realism.frame(db)) == {"n": 0}
    out = Outbox()
    fade.on_alert(db, alert("OKD"), now=T10)
    fade.on_alert(db, alert("SIL", mid=2), now=T10)
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(seconds=15))
    okd = next(t for t in out if bold("OKD") in t)
    ap.answer(db, ap.Command("OK", code_in(okd)), "SM1", now=T10 + timedelta(minutes=1))
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(minutes=1, seconds=10))   # OKD enters at 10.00
    fade.monitor(db, out, lambda s: 10.0, now=T10 + timedelta(minutes=4, seconds=20))   # SIL auto-enters
    fade.monitor(db, out, lambda s: 9.0, now=T10 + timedelta(minutes=70))                # both exit at 9.00 (+10%)
    ids = {r["symbol"]: r["id"] for r in rows(db)}

    def official(due):
        return pd.DataFrame([
            {"id": ids["OKD"], "qualifies": True, "entry_ts": "t", "entry": 10, "cover": 9.2, "cost_rt": 0.01,
             "gross": 0.08, "net": 0.065, "rule_entry": 10.5, "rule_net": 0.10},
            {"id": ids["SIL"], "qualifies": True, "entry_ts": "t", "entry": 10, "cover": 9.0, "cost_rt": 0.01,
             "gross": 0.10, "net": 0.085, "rule_entry": 10, "rule_net": 0.085}])
    fade.settle(db, official, now=T10 + timedelta(hours=3))
    df = realism.frame(db).set_index("symbol")
    assert df.loc["OKD", "mode"] == "approved" and df.loc["SIL", "mode"] == "auto"
    assert df.loc["OKD", "gap"] == pytest.approx(0.02) and df.loc["SIL", "gap"] == pytest.approx(0.0)
    assert df.loc["OKD", "latency_s"] == pytest.approx(70) and df.loc["SIL", "latency_s"] == pytest.approx(260)
    assert df.loc["OKD", "wait_cost"] == pytest.approx(-0.035) and df.loc["SIL", "wait_cost"] == pytest.approx(0)
    s = realism.summary(realism.frame(db))
    assert s["n"] == 2 and s["modes"] == {"approved": 1, "auto": 1} and s["wait_cost_mean"] == pytest.approx(-0.0175)
    line = realism.lines(db)[0]
    assert line.startswith("fade exec: 2 trades (1 approved, 1 auto); texted vs official +1.00%/trade")
    assert "alert->entry median 2m45s" in line and "waiting cost -1.75%/trade" in line
