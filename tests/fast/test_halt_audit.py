from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_halt_audit as ha  # noqa: E402


def test_omh_like_tape_halt_then_reopen_above_stop():
    ts = pd.to_datetime(["2026-11-09 14:38", "2026-11-09 14:48", "2026-11-09 14:59", "2026-11-09 15:30"], utc=True)
    bars = pd.DataFrame({"ts": ts, "open": [24.6, 29.2, 32.8, 30.0], "high": [27.06, 31.33, 32.8, 30.5],
                         "low": [24.0, 26.28, 30.0, 29.0], "close": [27.06, 28.03, 32.71, 30.0]})
    x = ha.live_trade(bars, pd.Timestamp("2026-11-09 14:38:18", tz="UTC"))
    assert x["entry"] == 27.06 and x["gap_min"] == 9                     # 09:39-09:47 no prints
    # stop level 32.472: 09:48 high 31.33 is below it; the 09:59 reopen at 32.80 gaps over it -> cover at the reopen
    assert x["stopped"] and x["cover"] == pytest.approx(32.8)
    assert x["gross"] == pytest.approx((27.06 - 32.8) / 27.06)


def test_no_bar_at_alert_is_not_tradable():
    bars = pd.DataFrame({"ts": pd.to_datetime(["2026-11-09 14:50"], utc=True), "open": [1.0], "high": [1.0],
                         "low": [1.0], "close": [1.0]})
    assert ha.live_trade(bars, pd.Timestamp("2026-11-09 14:38:18", tz="UTC")) is None
