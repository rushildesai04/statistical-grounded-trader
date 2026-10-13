"""Momentum family: RSI (Wilder), MACD, ROC."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.core.interfaces import IndicatorSpec
from sgt.core.types import Bar

from .base import NaN, BaseIndicator, EmaCore, WilderCore, ema_array


def _rsi_from_avgs(avg_gain: float, avg_loss: float) -> float:
    """RSI from Wilder average gain/loss. Documented edges:
    all gains (avg_loss == 0, avg_gain > 0) -> 100; flat (both 0) -> 50."""
    if avg_loss == 0.0:
        return 50.0 if avg_gain == 0.0 else 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


class Rsi(BaseIndicator):
    """Wilder RSI(n). First average gain/loss is the simple mean of the first ``n``
    changes, so the first value appears after ``n+1`` closes. warmup = n + 1."""

    def __init__(self, spec: IndicatorSpec, n: int) -> None:
        self._period = int(n)
        super().__init__(spec, ("value",), warmup=self._period + 1)

    def _init_state(self) -> None:
        self._prev_close: float | None = None
        self._gain = WilderCore(self._period)
        self._loss = WilderCore(self._period)

    def update(self, bar: Bar) -> None:
        self._n += 1
        c = bar.close
        if self._prev_close is None:
            self._prev_close = c
            self._vals["value"] = None
            return
        change = c - self._prev_close
        self._prev_close = c
        ag = self._gain.update(change if change > 0.0 else 0.0)
        al = self._loss.update(-change if change < 0.0 else 0.0)
        self._vals["value"] = _rsi_from_avgs(ag, al) if (ag is not None and al is not None) else None


class Macd(BaseIndicator):
    """MACD(fast, slow, signal). line = EMA_fast - EMA_slow (valid once both EMAs
    exist, i.e. at index slow-1); signal = EMA_signal(line) seeded with the SMA of
    the first ``signal`` line values; hist = line - signal.

    ``line`` becomes non-None before ``signal``/``hist``; ``ready`` (warmup =
    slow + signal - 1) marks when all three outputs are defined."""

    def __init__(self, spec: IndicatorSpec, fast: int, slow: int, signal: int) -> None:
        self._fast = int(fast)
        self._slow = int(slow)
        self._signal = int(signal)
        super().__init__(spec, ("line", "signal", "hist"), warmup=self._slow + self._signal - 1)

    def _init_state(self) -> None:
        self._fcore = EmaCore(self._fast)
        self._score = EmaCore(self._slow)
        self._sigcore = EmaCore(self._signal)

    def update(self, bar: Bar) -> None:
        self._n += 1
        c = bar.close
        f = self._fcore.update(c)
        s = self._score.update(c)
        line = sig = hist = None
        if f is not None and s is not None:
            line = f - s
            sig = self._sigcore.update(line)
            if sig is not None:
                hist = line - sig
        self._vals["line"], self._vals["signal"], self._vals["hist"] = line, sig, hist


class Roc(BaseIndicator):
    """Rate of change over ``n`` bars: (C_t - C_{t-n}) / C_{t-n} * 100. warmup = n+1.
    Guarded against a non-positive reference close (yields None)."""

    def __init__(self, spec: IndicatorSpec, n: int) -> None:
        self._period = int(n)
        super().__init__(spec, ("value",), warmup=self._period + 1)

    def _init_state(self) -> None:
        from collections import deque

        self._buf: deque[float] = deque(maxlen=self._period + 1)

    def update(self, bar: Bar) -> None:
        self._n += 1
        self._buf.append(bar.close)
        if len(self._buf) == self._period + 1:
            old = self._buf[0]
            self._vals["value"] = (bar.close - old) / old * 100.0 if old > 0.0 else None
        else:
            self._vals["value"] = None


# --------------------------------------------------------------------------- batch


def rsi_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    close = frame["close"].to_numpy(dtype=float)
    n = int(p["n"])
    out = np.full(close.shape[0], NaN)
    if close.shape[0] >= 2:
        change = np.diff(close)
        gain = np.where(change > 0.0, change, 0.0)
        loss = np.where(change < 0.0, -change, 0.0)
        ag = wilder_seeded(gain, n)
        al = wilder_seeded(loss, n)
        for i in range(change.shape[0]):
            if not np.isnan(ag[i]):
                out[i + 1] = _rsi_from_avgs(ag[i], al[i])
    return pd.DataFrame({"value": out}, index=frame.index)


def wilder_seeded(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder RMA over changes: first = mean of first n, matching WilderCore."""
    from .base import wilder_array

    return wilder_array(x, n)


def macd_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    close = frame["close"].to_numpy(dtype=float)
    fast, slow, signal = int(p["fast"]), int(p["slow"]), int(p["signal"])
    line = ema_array(close, fast) - ema_array(close, slow)
    sig = np.full(line.shape[0], NaN)
    hist = np.full(line.shape[0], NaN)
    valid = ~np.isnan(line)
    if valid.any():
        start = int(np.argmax(valid))  # first valid index (slow-1)
        seg = line[start:]
        sig[start:] = ema_array(seg, signal)
        hist = line - sig
    return pd.DataFrame({"line": line, "signal": sig, "hist": hist}, index=frame.index)


def roc_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    close = frame["close"].to_numpy(dtype=float)
    n = int(p["n"])
    out = np.full(close.shape[0], NaN)
    for i in range(n, close.shape[0]):
        old = close[i - n]
        if old > 0.0:
            out[i] = (close[i] - old) / old * 100.0
    return pd.DataFrame({"value": out}, index=frame.index)
