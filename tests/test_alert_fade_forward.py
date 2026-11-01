"""Forward-record bookkeeping for the alert fade (scripts/alert_fade_forward.py, prereg amendment F1)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import alert_fade_forward as fw  # noqa: E402


def trades(sessions: list[str], net: float) -> pd.DataFrame:
    return pd.DataFrame({"session": sessions, "net": net})


def test_month_summaries_only_finished_months_cumulative():
    t = trades(["2026-10-30", "2026-11-02", "2026-11-30", "2026-12-01"], 0.01)
    t.loc[1, "net"] = -0.03
    m = fw.month_summaries(t, date(2026, 12, 15))
    assert list(m) == ["2026-10", "2026-11"]                    # December not over
    assert m["2026-10"]["n"] == 1 and m["2026-11"]["n"] == 3      # cumulative
    assert abs(m["2026-11"]["mean"] - (-0.01 / 3)) < 1e-12


def test_decision_thresholds():
    rng = np.random.default_rng(0)
    days = [f"2026-11-{d:02d}" for d in range(1, 31)]
    assert fw.decision(trades(days * 5, 0.01), rng).startswith("collecting")
    assert fw.decision(trades(days * 10, -0.001), rng).startswith("DROP")
    assert fw.decision(trades(days * 10, 0.01), rng).startswith("KEEP")
    noisy = trades(days * 10, 0.0).assign(net=lambda d: np.where(np.arange(len(d)) % 2, 0.30, -0.29))
    assert fw.decision(noisy, rng).startswith("continue")
