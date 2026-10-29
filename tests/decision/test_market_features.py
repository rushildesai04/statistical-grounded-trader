"""Market context: only SPY bars closed before the decision; day return vs prior RTH close."""
from __future__ import annotations

import pandas as pd

from sgt.decision.market_features import MarketIndex


def test_point_in_time_market_returns(tmp_path):
    rows = [("2026-01-05T20:59:00Z", 100.0, "rth"),                     # prior session close
            ("2026-01-06T14:30:00Z", 101.0, "rth"), ("2026-01-06T14:59:00Z", 102.0, "rth"),
            ("2026-01-06T15:04:00Z", 103.0, "rth"), ("2026-01-06T15:05:00Z", 999.0, "rth")]
    p = tmp_path / "spy.parquet"
    pd.DataFrame(rows, columns=["ts", "close", "seg"]).assign(ts=lambda d: pd.to_datetime(d.ts, utc=True)).to_parquet(p)
    m = MarketIndex.from_parquet(str(p))
    f = m.features("X", pd.Timestamp("2026-01-06T15:05:00Z").value)    # bar 15:05 -> sees <= 15:04
    assert abs(f["f_mkt_ret_day"] - 0.03) < 1e-12                      # 103 / 100 - 1, not 999
    assert abs(f["f_mkt_ret_30m"] - (103 / 101 - 1)) < 1e-12
    assert abs(f["f_mkt_ret_5m"] - (103 / 102 - 1)) < 1e-12
