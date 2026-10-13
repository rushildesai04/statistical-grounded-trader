"""Shared primitives for the streaming indicator library.

Every indicator subclasses :class:`sgt.core.interfaces.Indicator` and does O(1)
work per bar on the streaming path (no pandas, no per-bar allocation of growing
buffers). Each indicator ships with a vectorized numpy ``*_batch`` function used
for research; a property test asserts streaming and batch agree to rtol 1e-9
with NaN (batch) matching None (streaming) position-for-position.

Numerical contract: wherever the streaming path accumulates a running sum, the
batch helper mirrors the *exact* same sequence of float additions/subtractions
(see :func:`running_sum` / :func:`running_sumsq`) so the two paths are not merely
close but produce identical intermediate roundings. Recursive smoothers (EMA,
Wilder RMA, PSAR, SuperTrend, VWAP, PVT) are likewise implemented once per path
with identical arithmetic.
"""
from __future__ import annotations

from collections import deque
from typing import Mapping

import numpy as np

from sgt.core.interfaces import Indicator, IndicatorSpec

NaN = float("nan")


class BaseIndicator(Indicator):
    """Common scaffolding: output storage, warmup-based readiness, reset.

    Subclasses set state in :meth:`_init_state`, mutate ``self._vals`` inside
    :meth:`update`, and bump ``self._n`` once per bar. ``values()`` reflects the
    true per-output availability (None where the output is not yet defined),
    which is the ground truth the batch functions must match. ``ready``/``warmup``
    describe *full* readiness (all outputs defined); a multi-output indicator can
    expose an earlier output (e.g. MACD ``line``) before ``ready`` flips True.
    """

    def __init__(self, spec: IndicatorSpec, outputs: tuple[str, ...], warmup: int) -> None:
        self.spec = spec
        self.outputs = tuple(outputs)
        self.warmup = int(warmup)
        self._n = 0
        self._vals: dict[str, float | None] = {o: None for o in self.outputs}
        self._init_state()

    # --- subclass hooks ---------------------------------------------------
    def _init_state(self) -> None:  # pragma: no cover - overridden
        """Allocate/zero all incremental state. Called by __init__ and reset()."""

    # --- Indicator API ----------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._n >= self.warmup

    def values(self) -> Mapping[str, float | None]:
        return dict(self._vals)

    def reset(self) -> None:
        self._n = 0
        for o in self.outputs:
            self._vals[o] = None
        self._init_state()


# --------------------------------------------------------------------------- cores
# Lightweight non-Indicator smoothers reused by composite indicators (MACD, EWO,
# cross-strength, SuperTrend, ...). Plain Python so they stay on the O(1) path.


class EmaCore:
    """Incremental EMA with SMA seed.

    ``alpha = 2/(n+1)``; the EMA is seeded with the simple mean of the first ``n``
    inputs (common charting-platform convention, e.g. Webull/TradingView) and
    first becomes non-None at the ``n``-th input. EMA(1) == the raw input.
    """

    __slots__ = ("n", "alpha", "_sum", "_count", "value")

    def __init__(self, n: int) -> None:
        if n < 1:
            raise ValueError(f"EMA period must be >= 1, got {n}")
        self.n = n
        self.alpha = 2.0 / (n + 1.0)
        self.reset()

    def reset(self) -> None:
        self._sum = 0.0
        self._count = 0
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self._sum += x
            self._count += 1
            if self._count == self.n:
                self.value = self._sum / self.n
        else:
            self.value = self.alpha * x + (1.0 - self.alpha) * self.value
        return self.value


class SmaCore:
    """Incremental simple moving average over a fixed window via running sum."""

    __slots__ = ("n", "_buf", "_sum", "value")

    def __init__(self, n: int) -> None:
        if n < 1:
            raise ValueError(f"SMA period must be >= 1, got {n}")
        self.n = n
        self.reset()

    def reset(self) -> None:
        self._buf: deque[float] = deque()
        self._sum = 0.0
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        self._buf.append(x)
        self._sum += x
        if len(self._buf) > self.n:
            self._sum -= self._buf.popleft()
        if len(self._buf) == self.n:
            self.value = self._sum / self.n
        return self.value


class WilderCore:
    """Wilder's RMA: first value is the simple mean of the first ``n`` inputs,
    then ``rma_t = rma_{t-1} + (x_t - rma_{t-1})/n`` (equivalently
    ``(rma_{t-1}*(n-1) + x_t)/n``). Used by RSI/ATR/ADX."""

    __slots__ = ("n", "_sum", "_count", "value")

    def __init__(self, n: int) -> None:
        if n < 1:
            raise ValueError(f"Wilder period must be >= 1, got {n}")
        self.n = n
        self.reset()

    def reset(self) -> None:
        self._sum = 0.0
        self._count = 0
        self.value: float | None = None

    def update(self, x: float) -> float | None:
        if self.value is None:
            self._sum += x
            self._count += 1
            if self._count == self.n:
                self.value = self._sum / self.n
        else:
            self.value = (self.value * (self.n - 1) + x) / self.n
        return self.value


# --------------------------------------------------------------------------- batch helpers


def ema_array(x: np.ndarray, n: int) -> np.ndarray:
    """Vectorized EMA matching :class:`EmaCore` exactly (SMA seed at index n-1)."""
    out = np.full(x.shape[0], NaN)
    if x.shape[0] < n:
        return out
    alpha = 2.0 / (n + 1.0)
    seed = x[:n].sum() / n
    out[n - 1] = seed
    prev = seed
    for i in range(n, x.shape[0]):
        prev = alpha * x[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def running_sum(x: np.ndarray, n: int) -> np.ndarray:
    """Running window sum mirroring :class:`SmaCore`'s add/subtract order exactly.

    NaN before the window is full (index < n-1).
    """
    out = np.full(x.shape[0], NaN)
    s = 0.0
    for i in range(x.shape[0]):
        s += x[i]
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s
    return out


def sma_array(x: np.ndarray, n: int) -> np.ndarray:
    return running_sum(x, n) / n


def wilder_array(x: np.ndarray, n: int) -> np.ndarray:
    """Vectorized Wilder RMA matching :class:`WilderCore` (first = mean of n)."""
    out = np.full(x.shape[0], NaN)
    if x.shape[0] < n:
        return out
    seed = x[:n].sum() / n
    out[n - 1] = seed
    prev = seed
    for i in range(n, x.shape[0]):
        prev = (prev * (n - 1) + x[i]) / n
        out[i] = prev
    return out
