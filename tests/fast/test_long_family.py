from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_family_study as lf  # noqa: E402

T0 = pd.Timestamp("2026-11-09 15:00", tz="UTC")


def bars(rows):
    ts = pd.date_range(T0, periods=len(rows), freq="1min", tz="UTC")
    return pd.DataFrame([{"ts": t, "open": o, "high": h, "low": lo, "close": c} for t, (o, h, lo, c) in zip(ts, rows)])


def test_target_stop_timeout_and_stop_first_in_one_bar():
    assert lf.long_trade(bars([(10, 10.2, 9.9, 10.1), (10.1, 10.6, 10.0, 10.5)]), T0) == pytest.approx(0.05)
    assert lf.long_trade(bars([(10, 10.1, 9.4, 9.6)]), T0) == pytest.approx(-0.05)
    assert lf.long_trade(bars([(10, 10.6, 9.4, 10.0)]), T0) == pytest.approx(-0.05)          # both: stop first
    assert lf.long_trade(bars([(10, 10.1, 9.9, 10.0)] * 20), T0) == pytest.approx(0.0)       # 15-min close
    assert lf.long_trade(bars([(10, 10, 10, 10), (9.0, 9.1, 8.9, 9.0)]), T0) == pytest.approx(-0.10)  # gap down
    assert lf.long_trade(bars([(10, 10, 10, 10)]), T0 + pd.Timedelta(minutes=5)) is None
