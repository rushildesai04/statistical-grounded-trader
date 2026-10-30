"""F2 signal and the forward paper script (prereg 2026-10-29), synthetic prices only."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import forward_paper as fp  # noqa: E402

from sgt.allocation.signals import sixty_forty_trend  # noqa: E402


def _prices(end="2027-01-29", spy_drift=0.0005, ief_drift=0.0002, shy_drift=0.0001):
    idx = pd.bdate_range("2025-06-02", end)
    t = np.arange(len(idx))
    drift = {s: 0.0003 for s in fp.SYMBOLS} | {"SPY": spy_drift, "IEF": ief_drift, "SHY": shy_drift}
    return pd.DataFrame({s: 100 * np.exp(d * t) for s, d in drift.items()}, index=idx)


def test_sixty_forty_trend_legs():
    px = _prices()
    t = px.index[-1]
    assert sixty_forty_trend(px[["SPY", "IEF", "SHY"]], "SHY", t).to_dict() == {"SPY": 0.6, "IEF": 0.4}
    down = _prices(spy_drift=-0.0005)
    assert sixty_forty_trend(down[["SPY", "IEF", "SHY"]], "SHY", t).to_dict() == {"SPY": 0.0, "IEF": 0.4}


def test_completed_signals_skip_current_month():
    px = _prices(end="2026-12-15")
    assert [str(t.date()) for t in fp.completed_signals(px)] == ["2026-10-30", "2026-11-30"]


def test_snapshot_append_only_and_verifies(tmp_path, monkeypatch):
    px = _prices()
    monkeypatch.setattr(fp, "OUT", tmp_path)
    monkeypatch.setattr(fp, "load_prices", lambda: px)
    fp.snapshot()
    files = sorted(p.name for p in tmp_path.glob("*.json"))
    assert files == ["2026-11.json", "2026-12.json", "2027-01.json"]
    rec = json.loads((tmp_path / "2026-11.json").read_text())
    assert rec["weights"]["B_6040"] == {"SPY": 0.6, "IEF": 0.4}
    assert abs(sum(rec["weights"]["F1_dual_momentum"].values()) - 1.0) < 1e-9
    before = (tmp_path / "2026-11.json").read_text()
    fp.snapshot()
    assert (tmp_path / "2026-11.json").read_text() == before
    assert fp._verify_snapshots(px) == []
    rec["weights"]["B_6040"] = {"SPY": 1.0}
    (tmp_path / "2026-11.json").write_text(json.dumps(rec))
    assert fp._verify_snapshots(px)


def test_curves_start_at_first_execution():
    nav = fp.sleeve_curves(_prices())
    assert nav.index[0] == pd.Timestamp("2026-11-02")
    assert nav["B_6040"].iloc[0] == pytest.approx(fp.NAV0 * (1 - 2 * 1.0 * 5 / 1e4))
