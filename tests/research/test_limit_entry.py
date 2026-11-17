"""Plumbing of prereg_fade_limit_entry_2026-11-17 on synthetic bars (the real run is made once)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_limit_entry_study as st  # noqa: E402

T0 = pd.Timestamp("2026-03-03T15:00:00Z")


def bars(rows, skip=()):
    """rows = (open, high, low, close) per minute from T0."""
    f = pd.DataFrame([{"ts": T0 + pd.Timedelta(minutes=i), "open": o, "high": h, "low": lo, "close": c, "volume": 100.0}
                      for i, (o, h, lo, c) in enumerate(rows)])
    return f.drop(index=list(skip)).reset_index(drop=True)


FLAT = (10.0, 10.0, 10.0, 10.0)


def test_the_offer_fills_only_when_the_tape_trades_through_it():
    touch = bars([(10.0, 10.5, 10.0, 10.2)] + [FLAT] * 10)                  # high exactly at the 5% offer: no fill
    assert st.limit_entry(touch, T0, 10.0, 0.05, 3) == (T0 + pd.Timedelta(minutes=3), 10.0, "late")
    through = bars([(10.0, 10.6, 10.0, 10.2)] + [FLAT] * 10)                # traded through: filled at the offer
    ts, px, how = st.limit_entry(through, T0, 10.0, 0.05, 3)
    assert (ts, how) == (T0, "limit") and px == pytest.approx(10.5)
    second = bars([FLAT, (10.0, 10.9, 10.0, 10.8), FLAT, FLAT, FLAT])        # fills in the second minute
    ts, px, how = st.limit_entry(second, T0, 10.0, 0.05, 3)
    assert (ts, how) == (T0 + pd.Timedelta(minutes=1), "limit") and px == pytest.approx(10.5)
    gap = bars([FLAT, (10.8, 11.0, 10.7, 10.9), FLAT, FLAT, FLAT])           # opens above the offer: filled at the open
    assert st.limit_entry(gap, T0, 10.0, 0.05, 3) == (T0 + pd.Timedelta(minutes=1), 10.8, "limit")


def test_no_fill_inside_the_window_is_a_late_market_entry():
    late = bars([FLAT, FLAT, FLAT, (9.0, 11.0, 9.0, 9.5), FLAT])            # the squeeze comes after the 3 minutes
    assert st.limit_entry(late, T0, 10.0, 0.05, 3) == (T0 + pd.Timedelta(minutes=3), 9.0, "late")
    assert st.limit_entry(late, T0, 10.0, 0.05, 5)[2] == "limit"            # a 5-minute offer catches it
    halted = bars([FLAT] * 20, skip=range(1, 12))                           # nothing prints for 11 minutes
    assert st.limit_entry(halted, T0, 10.0, 0.05, 3) is None


def frame(n=240, fill_edge=0.0, seed=0):
    rng = np.random.default_rng(seed)
    days = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2025-01-02", periods=n // 3)]
    champ = rng.normal(0.01, 0.10, n)
    via = np.where(rng.random(n) < 0.4, "limit", "late")
    t = pd.DataFrame({"symbol": [f"S{i}" for i in range(n)], "session": [days[i % len(days)] for i in range(n)],
                      "net_champion": champ, "how_champion": "cap"})
    for name, _, _ in st.VARIANTS:
        t[f"net_{name}"] = champ + np.where(via == "limit", fill_edge, -0.002) + rng.normal(0, 0.005, n)
        t[f"how_{name}"] = "cap"
        t[f"via_{name}"] = via
    return t


def test_gate_and_the_adverse_selection_table(monkeypatch):
    monkeypatch.setattr(st, "N_BOOT", 500)
    good = st.evaluate(frame(fill_edge=0.05))
    r = good["LIM-5-3"]
    assert r["pass"] is True and r["limit"]["variant"] - r["limit"]["champion"] == pytest.approx(0.05, abs=0.005)
    assert r["late"]["variant"] < r["late"]["champion"] and 0.3 < r["filled_share"] < 0.5
    assert r["limit_only"]["n"] == r["limit"]["n"]
    assert not any(v["pass"] for v in st.evaluate(frame(fill_edge=0.0)).values())
    assert not any(v["pass"] for v in st.evaluate(frame(fill_edge=-0.05)).values())


def test_main_refuses_a_second_run(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "OUT", tmp_path)
    (tmp_path / "dev.json").write_text("{}")
    called = []
    monkeypatch.setattr(st, "events", lambda split: called.append(split))
    assert st.main() == 2 and called == []
