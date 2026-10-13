"""Volatility family: ATR (Wilder) and Bollinger bandwidth (BBW)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from sgt.core.interfaces import IndicatorSpec
from sgt.core.types import Bar

from .base import NaN, BaseIndicator, WilderCore, running_sum


def true_range(high: float, low: float, prev_close: float | None) -> float:
    """TR with the first bar's TR = high - low (no prior close)."""
    if prev_close is None:
        return high - low
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


class Atr(BaseIndicator):
    """Wilder ATR(n). TR_0 = high-low; first ATR = mean of the first ``n`` TRs at
    index n-1, then Wilder-smoothed. warmup = n."""

    def __init__(self, spec: IndicatorSpec, n: int) -> None:
        self._period = int(n)
        super().__init__(spec, ("value",), warmup=self._period)

    def _init_state(self) -> None:
        self._prev_close: float | None = None
        self._core = WilderCore(self._period)

    def update(self, bar: Bar) -> None:
        self._n += 1
        tr = true_range(bar.high, bar.low, self._prev_close)
        self._prev_close = bar.close
        self._vals["value"] = self._core.update(tr)


class Bbw(BaseIndicator):
    """Bollinger bandwidth(n, k) over close with population std.

    middle = SMA(n); std = population std over the window; upper/lower = middle +/-
    k*std. Outputs ``width = (upper-lower)/middle = 2k*std/middle`` and
    ``percent_b = (close-lower)/(upper-lower)``. Guards (documented): zero-width
    band -> percent_b None; non-positive middle -> width None. warmup = n."""

    def __init__(self, spec: IndicatorSpec, n: int, k: float) -> None:
        self._period = int(n)
        self._k = float(k)
        super().__init__(spec, ("width", "percent_b"), warmup=self._period)

    def _init_state(self) -> None:
        from collections import deque

        self._buf: deque[float] = deque()
        self._sum = 0.0
        self._sumsq = 0.0

    def update(self, bar: Bar) -> None:
        self._n += 1
        c = bar.close
        self._buf.append(c)
        self._sum += c
        self._sumsq += c * c
        if len(self._buf) > self._period:
            old = self._buf.popleft()
            self._sum -= old
            self._sumsq -= old * old
        if len(self._buf) == self._period:
            mean = self._sum / self._period
            var = self._sumsq / self._period - mean * mean
            std = math.sqrt(var) if var > 0.0 else 0.0
            upper = mean + self._k * std
            lower = mean - self._k * std
            span = upper - lower
            self._vals["width"] = (span / mean) if mean > 0.0 else None
            self._vals["percent_b"] = ((c - lower) / span) if span > 0.0 else None
        else:
            self._vals["width"] = None
            self._vals["percent_b"] = None


# --------------------------------------------------------------------------- batch


def atr_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    return pd.DataFrame({"value": _atr_array(high, low, close, int(p["n"]))}, index=frame.index)


def _atr_array(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int) -> np.ndarray:
    from .base import wilder_array

    m = high.shape[0]
    tr = np.empty(m)
    if m:
        tr[0] = high[0] - low[0]
    for i in range(1, m):
        pc = close[i - 1]
        tr[i] = max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc))
    return wilder_array(tr, n)


def bbw_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    close = frame["close"].to_numpy(dtype=float)
    n, k = int(p["n"]), float(p["k"])
    s = running_sum(close, n)
    s2 = running_sum(close * close, n)
    width = np.full(close.shape[0], NaN)
    pct = np.full(close.shape[0], NaN)
    for i in range(close.shape[0]):
        if np.isnan(s[i]):
            continue
        mean = s[i] / n
        var = s2[i] / n - mean * mean
        std = math.sqrt(var) if var > 0.0 else 0.0
        upper = mean + k * std
        lower = mean - k * std
        span = upper - lower
        if mean > 0.0:
            width[i] = span / mean
        if span > 0.0:
            pct[i] = (close[i] - lower) / span
    return pd.DataFrame({"width": width, "percent_b": pct}, index=frame.index)
