"""Volume/flow family: PVT, VWAP (session/rth), relative volume, float rotation."""
from __future__ import annotations

from collections import deque

import numpy as np
import pandas as pd

from sgt.core.interfaces import IndicatorSpec
from sgt.core.types import Bar, Segment

from .base import NaN, BaseIndicator, EmaCore, SmaCore, ema_array, sma_array


class Pvt(BaseIndicator):
    """Price-Volume Trend with a smoothed signal.

    ``PVT_0 = 0``; ``PVT_t = PVT_{t-1} + V_t*(C_t - C_{t-1})/C_{t-1}`` (the prior
    close is guaranteed > 0 by the Bar contract; guarded anyway). The signal is an
    SMA or EMA of PVT over ``signal_n`` and first appears at index signal_n-1.
    Outputs ``pvt`` (from bar 0) and ``signal``. warmup = signal_n."""

    def __init__(self, spec: IndicatorSpec, signal_n: int, signal_kind: str) -> None:
        self._signal_n = int(signal_n)
        self._signal_kind = str(signal_kind)
        if self._signal_kind not in ("sma", "ema"):
            raise ValueError(f"pvt signal_kind must be sma|ema, got {signal_kind!r}")
        super().__init__(spec, ("pvt", "signal"), warmup=self._signal_n)

    def _init_state(self) -> None:
        self._prev_close: float | None = None
        self._pvt = 0.0
        self._sig = EmaCore(self._signal_n) if self._signal_kind == "ema" else SmaCore(self._signal_n)

    def update(self, bar: Bar) -> None:
        self._n += 1
        c = bar.close
        if self._prev_close is None:
            self._pvt = 0.0
        elif self._prev_close > 0.0:
            self._pvt += bar.volume * (c - self._prev_close) / self._prev_close
        self._prev_close = c
        self._vals["pvt"] = self._pvt
        self._vals["signal"] = self._sig.update(self._pvt)


class Vwap(BaseIndicator):
    """Volume-weighted average price using typical price (h+l+c)/3.

    ``anchor='session'`` accumulates every segment and resets at each new
    ``session_date``. ``anchor='rth'`` resets at the first RTH bar of a session and
    is None before RTH; post-RTH bars carry the RTH value without adding post
    volume. Zero cumulative volume -> None."""

    def __init__(self, spec: IndicatorSpec, anchor: str) -> None:
        self._anchor = str(anchor)
        if self._anchor not in ("session", "rth"):
            raise ValueError(f"vwap anchor must be session|rth, got {anchor!r}")
        super().__init__(spec, ("value",), warmup=1)

    def _init_state(self) -> None:
        self._session = None
        self._cum_pv = 0.0
        self._cum_v = 0.0
        self._started = False

    @property
    def ready(self) -> bool:
        return self._vals["value"] is not None

    def update(self, bar: Bar) -> None:
        self._n += 1
        if bar.session_date != self._session:
            self._session = bar.session_date
            self._cum_pv = 0.0
            self._cum_v = 0.0
            self._started = False
        tp = (bar.high + bar.low + bar.close) / 3.0
        if self._anchor == "session":
            self._cum_pv += tp * bar.volume
            self._cum_v += bar.volume
            self._vals["value"] = (self._cum_pv / self._cum_v) if self._cum_v > 0.0 else None
        else:  # rth
            if bar.segment == Segment.RTH:
                if not self._started:
                    self._started = True
                    self._cum_pv = 0.0
                    self._cum_v = 0.0
                self._cum_pv += tp * bar.volume
                self._cum_v += bar.volume
            if not self._started:
                self._vals["value"] = None
            else:
                self._vals["value"] = (self._cum_pv / self._cum_v) if self._cum_v > 0.0 else None


class RelVol(BaseIndicator):
    """Relative volume vs the same minute-of-day over prior sessions.

    ``value`` = this bar's volume / mean volume at the same ``minute_of_day`` over
    the previous ``lookback_sessions`` sessions that had a bar at that minute
    (strictly prior sessions; the current session is never included -> no
    look-ahead). ``cum`` = cumulative session volume / mean cumulative volume at
    the same minute over those prior sessions. None until >= 1 prior observation
    for that minute, or when the prior mean is 0. ``ready`` once >= 1 session has
    completed."""

    def __init__(self, spec: IndicatorSpec, lookback_sessions: int) -> None:
        self._lookback = int(lookback_sessions)
        super().__init__(spec, ("value", "cum"), warmup=1)

    def _init_state(self) -> None:
        self._hist_vol: dict[int, deque[float]] = {}
        self._hist_cum: dict[int, deque[float]] = {}
        self._session = None
        self._cur_cum = 0.0
        self._pending: dict[int, tuple[float, float]] = {}
        self._completed = 0

    @property
    def ready(self) -> bool:
        return self._completed >= 1

    def _flush(self) -> None:
        if not self._pending:
            return
        for minute, (vol, cum) in self._pending.items():
            self._hist_vol.setdefault(minute, deque(maxlen=self._lookback)).append(vol)
            self._hist_cum.setdefault(minute, deque(maxlen=self._lookback)).append(cum)
        self._completed += 1
        self._pending = {}

    def update(self, bar: Bar) -> None:
        self._n += 1
        if bar.session_date != self._session:
            self._flush()
            self._session = bar.session_date
            self._cur_cum = 0.0
        self._cur_cum += bar.volume
        m = bar.minute_of_day
        hv = self._hist_vol.get(m)
        if hv:
            mean = sum(hv) / len(hv)
            self._vals["value"] = (bar.volume / mean) if mean > 0.0 else None
        else:
            self._vals["value"] = None
        hc = self._hist_cum.get(m)
        if hc:
            mean_c = sum(hc) / len(hc)
            self._vals["cum"] = (self._cur_cum / mean_c) if mean_c > 0.0 else None
        else:
            self._vals["cum"] = None
        self._pending[m] = (bar.volume, self._cur_cum)


class FloatRotation(BaseIndicator):
    """Cumulative session volume / free float (resets each session). None when the
    free float is unknown or non-positive. ``ready`` once a positive float is set."""

    def __init__(self, spec: IndicatorSpec, free_float: float | None) -> None:
        self._free_float = float(free_float) if free_float is not None else None
        super().__init__(spec, ("value",), warmup=1)

    def _init_state(self) -> None:
        self._session = None
        self._cum = 0.0

    @property
    def ready(self) -> bool:
        return self._free_float is not None and self._free_float > 0.0

    def update(self, bar: Bar) -> None:
        self._n += 1
        if bar.session_date != self._session:
            self._session = bar.session_date
            self._cum = 0.0
        self._cum += bar.volume
        if self._free_float is not None and self._free_float > 0.0:
            self._vals["value"] = self._cum / self._free_float
        else:
            self._vals["value"] = None


# --------------------------------------------------------------------------- batch


def pvt_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    close = frame["close"].to_numpy(dtype=float)
    volume = frame["volume"].to_numpy(dtype=float)
    m = close.shape[0]
    pvt = np.zeros(m)
    for i in range(1, m):
        prev = close[i - 1]
        pvt[i] = pvt[i - 1] + (volume[i] * (close[i] - prev) / prev if prev > 0.0 else 0.0)
    if m == 0:
        pvt = np.zeros(0)
    signal_n = int(p["signal_n"])
    if str(p["signal_kind"]) == "ema":
        sig = ema_array(pvt, signal_n)
    else:
        sig = sma_array(pvt, signal_n)
    return pd.DataFrame({"pvt": pvt, "signal": sig}, index=frame.index)


def vwap_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    volume = frame["volume"].to_numpy(dtype=float)
    sessions = list(frame["session_date"])
    segments = list(frame["segment"])
    anchor = str(p["anchor"])
    m = high.shape[0]
    out = np.full(m, NaN)
    cur_session = None
    cum_pv = cum_v = 0.0
    started = False
    for i in range(m):
        if sessions[i] != cur_session:
            cur_session = sessions[i]
            cum_pv = cum_v = 0.0
            started = False
        tp = (high[i] + low[i] + close[i]) / 3.0
        if anchor == "session":
            cum_pv += tp * volume[i]
            cum_v += volume[i]
            if cum_v > 0.0:
                out[i] = cum_pv / cum_v
        else:
            if segments[i] == Segment.RTH:
                if not started:
                    started = True
                    cum_pv = cum_v = 0.0
                cum_pv += tp * volume[i]
                cum_v += volume[i]
            if started and cum_v > 0.0:
                out[i] = cum_pv / cum_v
    return pd.DataFrame({"value": out}, index=frame.index)


def relvol_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    volume = frame["volume"].to_numpy(dtype=float)
    sessions = list(frame["session_date"])
    minutes = list(frame["minute_of_day"])
    lookback = int(p["lookback_sessions"])
    m = volume.shape[0]
    value = np.full(m, NaN)
    cum_out = np.full(m, NaN)
    hist_vol: dict[int, deque[float]] = {}
    hist_cum: dict[int, deque[float]] = {}
    cur_session = None
    cur_cum = 0.0
    pending: dict[int, tuple[float, float]] = {}
    for i in range(m):
        if sessions[i] != cur_session:
            for minute, (vol, cum) in pending.items():
                hist_vol.setdefault(minute, deque(maxlen=lookback)).append(vol)
                hist_cum.setdefault(minute, deque(maxlen=lookback)).append(cum)
            pending = {}
            cur_session = sessions[i]
            cur_cum = 0.0
        cur_cum += volume[i]
        mm = int(minutes[i])
        hv = hist_vol.get(mm)
        if hv:
            mean = sum(hv) / len(hv)
            if mean > 0.0:
                value[i] = volume[i] / mean
        hc = hist_cum.get(mm)
        if hc:
            mean_c = sum(hc) / len(hc)
            if mean_c > 0.0:
                cum_out[i] = cur_cum / mean_c
        pending[mm] = (volume[i], cur_cum)
    return pd.DataFrame({"value": value, "cum": cum_out}, index=frame.index)


def float_rotation_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    volume = frame["volume"].to_numpy(dtype=float)
    sessions = list(frame["session_date"])
    free_float = None
    if ctx is not None and getattr(ctx, "reference", None) is not None:
        free_float = ctx.reference.free_float
    m = volume.shape[0]
    out = np.full(m, NaN)
    cur_session = None
    cum = 0.0
    valid = free_float is not None and free_float > 0.0
    for i in range(m):
        if sessions[i] != cur_session:
            cur_session = sessions[i]
            cum = 0.0
        cum += volume[i]
        if valid:
            out[i] = cum / free_float
    return pd.DataFrame({"value": out}, index=frame.index)
