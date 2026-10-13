"""Moving-average family: SMA, EMA, EWO (Elliott Wave Oscillator)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.core.interfaces import IndicatorSpec
from sgt.core.types import Bar

from .base import BaseIndicator, EmaCore, SmaCore, ema_array, sma_array


class Sma(BaseIndicator):
    """Simple moving average of close over ``n`` bars. warmup = n."""

    def __init__(self, spec: IndicatorSpec, n: int) -> None:
        self._period = int(n)
        super().__init__(spec, ("value",), warmup=self._period)

    def _init_state(self) -> None:
        self._core = SmaCore(self._period)

    def update(self, bar: Bar) -> None:
        self._n += 1
        self._vals["value"] = self._core.update(bar.close)


class Ema(BaseIndicator):
    """Exponential moving average of close. ``alpha = 2/(n+1)``, seeded with the
    SMA of the first ``n`` closes (matches common charting platforms). warmup = n."""

    def __init__(self, spec: IndicatorSpec, n: int) -> None:
        self._period = int(n)
        super().__init__(spec, ("value",), warmup=self._period)

    def _init_state(self) -> None:
        self._core = EmaCore(self._period)

    def update(self, bar: Bar) -> None:
        self._n += 1
        self._vals["value"] = self._core.update(bar.close)


class Ewo(BaseIndicator):
    """Elliott Wave Oscillator: SMA(fast) - SMA(slow) of close. warmup = slow."""

    def __init__(self, spec: IndicatorSpec, fast: int, slow: int) -> None:
        self._fast = int(fast)
        self._slow = int(slow)
        super().__init__(spec, ("value",), warmup=self._slow)

    def _init_state(self) -> None:
        self._fcore = SmaCore(self._fast)
        self._score = SmaCore(self._slow)

    def update(self, bar: Bar) -> None:
        self._n += 1
        f = self._fcore.update(bar.close)
        s = self._score.update(bar.close)
        self._vals["value"] = (f - s) if (f is not None and s is not None) else None


# --------------------------------------------------------------------------- batch


def sma_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    close = frame["close"].to_numpy(dtype=float)
    return pd.DataFrame({"value": sma_array(close, int(p["n"]))}, index=frame.index)


def ema_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    close = frame["close"].to_numpy(dtype=float)
    return pd.DataFrame({"value": ema_array(close, int(p["n"]))}, index=frame.index)


def ewo_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    close = frame["close"].to_numpy(dtype=float)
    val = sma_array(close, int(p["fast"])) - sma_array(close, int(p["slow"]))
    return pd.DataFrame({"value": val}, index=frame.index)
