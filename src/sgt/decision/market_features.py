"""Point-in-time market-context features from SPY 1-minute bars (2026-10-28).

At a candidate's decision time (the close of its bar) the market tape is known up to that
minute: today's move versus yesterday's regular-session close and the last 5/30 minutes.
Small-cap momentum behaves differently on risk-on and risk-off days, which price/volume of
the stock alone cannot see. Research-only until the live runtime receives a SPY stream.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

import numpy as np
import pandas as pd

MARKET_FEATURES = ("f_mkt_ret_day", "f_mkt_ret_30m", "f_mkt_ret_5m")
_MIN_NS = 60 * 10**9


@dataclass
class MarketIndex:
    names = MARKET_FEATURES
    ts: np.ndarray          # bar-open ns, sorted
    close: np.ndarray
    day_keys: np.ndarray    # ET session date as int yyyymmdd per bar
    prev_close: dict        # yyyymmdd -> previous session's last RTH close

    @classmethod
    def from_parquet(cls, path: str) -> "MarketIndex":
        df = pd.read_parquet(path).sort_values("ts")
        ts = pd.to_datetime(df["ts"], utc=True)
        et = ts.dt.tz_convert("America/New_York")
        keys = (et.dt.year * 10000 + et.dt.month * 100 + et.dt.day).to_numpy()
        rth_last = df[df["seg"] == "rth"].assign(k=keys[df["seg"].to_numpy() == "rth"]) \
            .groupby("k")["close"].last()
        days = rth_last.index.to_numpy()
        prev = {int(days[i]): float(rth_last.iloc[i - 1]) for i in range(1, len(days))}
        return cls(ts.dt.as_unit("ns").astype("int64").to_numpy(), df["close"].to_numpy(float),
                   keys, prev)

    def features(self, symbol: str, bar_open_ns: int) -> dict[str, float]:
        out = {f: float("nan") for f in MARKET_FEATURES}
        cutoff = bar_open_ns            # SPY bars that OPENED before this bar opened are closed
        k = bisect_right(self.ts, cutoff - _MIN_NS)
        if k == 0:
            return out
        px = self.close[k - 1]
        pc = self.prev_close.get(int(self.day_keys[k - 1]))
        if pc:
            out["f_mkt_ret_day"] = px / pc - 1.0
        for name, mins in (("f_mkt_ret_30m", 30), ("f_mkt_ret_5m", 5)):
            j = bisect_right(self.ts, cutoff - (mins + 1) * _MIN_NS)
            if j > 0 and self.day_keys[j - 1] == self.day_keys[k - 1]:
                out[name] = px / self.close[j - 1] - 1.0
        return out
