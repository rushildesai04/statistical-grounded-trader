"""Trend family: Parabolic SAR, ADX (Wilder), SuperTrend, cross-strength."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.core.interfaces import IndicatorSpec
from sgt.core.types import Bar

from .base import NaN, BaseIndicator, EmaCore, WilderCore, ema_array
from .volatility import _atr_array, true_range


class Psar(BaseIndicator):
    """Wilder Parabolic SAR(start, step, max).

    Initial trend from the first two closes; SAR seed = the first bar's low (up)
    or high (down), EP = the second bar's high (up) or low (down), AF = start.
    Each bar: SAR += AF*(EP-SAR), SAR clamped outside the prior two bars' range,
    EP/AF advanced on a new extreme, and on penetration the trend flips with SAR
    reset to the prior EP and AF reset to start. Outputs ``sar`` and ``trend``
    (+1 long / -1 short). warmup = 2."""

    def __init__(self, spec: IndicatorSpec, start: float, step: float, max: float) -> None:
        self._start = float(start)
        self._step = float(step)
        self._max = float(max)
        super().__init__(spec, ("sar", "trend"), warmup=2)

    def _init_state(self) -> None:
        self._seen = 0
        self._sar = self._ep = self._af = None
        self._trend = 0
        self._c0 = self._h0 = self._l0 = None
        self._ph1 = self._pl1 = None  # prior bar high/low
        self._ph2 = self._pl2 = None  # two-bars-ago high/low

    def update(self, bar: Bar) -> None:
        self._n += 1
        h, l, c = bar.high, bar.low, bar.close
        if self._seen == 0:
            self._seen = 1
            self._h0, self._l0, self._c0 = h, l, c
            self._vals["sar"] = self._vals["trend"] = None
            return
        if self._seen == 1:
            self._seen = 2
            if c > self._c0:
                self._trend = 1
                self._sar = self._l0
                self._ep = h
            else:
                self._trend = -1
                self._sar = self._h0
                self._ep = l
            self._af = self._start
            self._ph2, self._pl2 = self._h0, self._l0
            self._ph1, self._pl1 = h, l
            self._vals["sar"] = self._sar
            self._vals["trend"] = float(self._trend)
            return
        sar = self._sar + self._af * (self._ep - self._sar)
        if self._trend == 1:
            sar = min(sar, self._pl1, self._pl2)
            if h > self._ep:
                self._ep = h
                self._af = min(self._af + self._step, self._max)
            if l < sar:
                self._trend = -1
                sar = self._ep
                self._ep = l
                self._af = self._start
        else:
            sar = max(sar, self._ph1, self._ph2)
            if l < self._ep:
                self._ep = l
                self._af = min(self._af + self._step, self._max)
            if h > sar:
                self._trend = 1
                sar = self._ep
                self._ep = h
                self._af = self._start
        self._sar = sar
        self._ph2, self._pl2 = self._ph1, self._pl1
        self._ph1, self._pl1 = h, l
        self._vals["sar"] = sar
        self._vals["trend"] = float(self._trend)


class Adx(BaseIndicator):
    """Wilder ADX(n) with directional indicators.

    +DI/-DI available from index n; ADX (Wilder RMA of DX) from index 2n-1.
    Outputs ``adx``, ``plus_di``, ``minus_di``. warmup = 2n. DX ratios are invariant
    to sum-vs-mean Wilder smoothing, so RMA cores are used throughout."""

    def __init__(self, spec: IndicatorSpec, n: int) -> None:
        self._period = int(n)
        super().__init__(spec, ("adx", "plus_di", "minus_di"), warmup=2 * self._period)

    def _init_state(self) -> None:
        self._ph = self._pl = self._pc = None
        self._pdm = WilderCore(self._period)
        self._mdm = WilderCore(self._period)
        self._tr = WilderCore(self._period)
        self._dx = WilderCore(self._period)

    def update(self, bar: Bar) -> None:
        self._n += 1
        h, l, c = bar.high, bar.low, bar.close
        if self._ph is None:
            self._ph, self._pl, self._pc = h, l, c
            self._vals["adx"] = self._vals["plus_di"] = self._vals["minus_di"] = None
            return
        up = h - self._ph
        dn = self._pl - l
        pdm_v = up if (up > dn and up > 0.0) else 0.0
        mdm_v = dn if (dn > up and dn > 0.0) else 0.0
        tr_v = max(h - l, abs(h - self._pc), abs(l - self._pc))
        self._ph, self._pl, self._pc = h, l, c
        sp = self._pdm.update(pdm_v)
        sm = self._mdm.update(mdm_v)
        st = self._tr.update(tr_v)
        if sp is None:
            self._vals["adx"] = self._vals["plus_di"] = self._vals["minus_di"] = None
            return
        pdi = 100.0 * sp / st if st > 0.0 else 0.0
        mdi = 100.0 * sm / st if st > 0.0 else 0.0
        dsum = pdi + mdi
        dx = 100.0 * abs(pdi - mdi) / dsum if dsum > 0.0 else 0.0
        adx = self._dx.update(dx)
        self._vals["plus_di"] = pdi
        self._vals["minus_di"] = mdi
        self._vals["adx"] = adx


class SuperTrend(BaseIndicator):
    """SuperTrend(n, mult) on Wilder ATR with final upper/lower band carry rules
    (TradingView/community formulation). Initialized on the first ATR bar in the
    downtrend state (supertrend = final upper band); self-corrects within a few
    bars. Outputs ``line`` and ``trend`` (+1/-1). warmup = n."""

    def __init__(self, spec: IndicatorSpec, n: int, mult: float) -> None:
        self._period = int(n)
        self._mult = float(mult)
        super().__init__(spec, ("line", "trend"), warmup=self._period)

    def _init_state(self) -> None:
        self._prev_close: float | None = None
        self._atr = WilderCore(self._period)
        self._pfu = self._pfl = None
        self._prev_c = None
        self._prev_in_upper = True
        self._started = False

    def update(self, bar: Bar) -> None:
        self._n += 1
        h, l, c = bar.high, bar.low, bar.close
        tr = true_range(h, l, self._prev_close)
        self._prev_close = c
        atr = self._atr.update(tr)
        if atr is None:
            self._vals["line"] = self._vals["trend"] = None
            return
        mid = (h + l) / 2.0
        bu = mid + self._mult * atr
        bl = mid - self._mult * atr
        if not self._started:
            fu, fl = bu, bl
            line = fu
            in_upper = True
            trend = -1
            self._started = True
        else:
            pfu, pfl, pc = self._pfu, self._pfl, self._prev_c
            fu = bu if (bu < pfu or pc > pfu) else pfu
            fl = bl if (bl > pfl or pc < pfl) else pfl
            if self._prev_in_upper:
                if c > fu:
                    line, in_upper, trend = fl, False, 1
                else:
                    line, in_upper, trend = fu, True, -1
            else:
                if c < fl:
                    line, in_upper, trend = fu, True, -1
                else:
                    line, in_upper, trend = fl, False, 1
        self._pfu, self._pfl, self._prev_c = fu, fl, c
        self._prev_in_upper = in_upper
        self._vals["line"] = line
        self._vals["trend"] = float(trend)


class CrossStrength(BaseIndicator):
    """(EMA_fast - EMA_slow) / ATR(atr_n). warmup = max(slow, atr_n). Guards a
    non-positive ATR (yields None)."""

    def __init__(self, spec: IndicatorSpec, fast: int, slow: int, atr_n: int) -> None:
        self._fast = int(fast)
        self._slow = int(slow)
        self._atr_n = int(atr_n)
        super().__init__(spec, ("value",), warmup=max(self._slow, self._atr_n))

    def _init_state(self) -> None:
        self._fcore = EmaCore(self._fast)
        self._score = EmaCore(self._slow)
        self._prev_close: float | None = None
        self._atr = WilderCore(self._atr_n)

    def update(self, bar: Bar) -> None:
        self._n += 1
        ef = self._fcore.update(bar.close)
        es = self._score.update(bar.close)
        tr = true_range(bar.high, bar.low, self._prev_close)
        self._prev_close = bar.close
        atr = self._atr.update(tr)
        if ef is not None and es is not None and atr is not None and atr > 0.0:
            self._vals["value"] = (ef - es) / atr
        else:
            self._vals["value"] = None


# --------------------------------------------------------------------------- batch


def psar_arrays(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                start: float, step: float, maxaf: float) -> tuple[np.ndarray, np.ndarray]:
    m = high.shape[0]
    sar = np.full(m, NaN)
    trend = np.full(m, NaN)
    if m < 2:
        return sar, trend
    if close[1] > close[0]:
        tr = 1
        s = low[0]
        ep = high[1]
    else:
        tr = -1
        s = high[0]
        ep = low[1]
    af = start
    sar[1] = s
    trend[1] = float(tr)
    ph2, pl2 = high[0], low[0]
    ph1, pl1 = high[1], low[1]
    for i in range(2, m):
        s = s + af * (ep - s)
        if tr == 1:
            s = min(s, pl1, pl2)
            if high[i] > ep:
                ep = high[i]
                af = min(af + step, maxaf)
            if low[i] < s:
                tr = -1
                s = ep
                ep = low[i]
                af = start
        else:
            s = max(s, ph1, ph2)
            if low[i] < ep:
                ep = low[i]
                af = min(af + step, maxaf)
            if high[i] > s:
                tr = 1
                s = ep
                ep = high[i]
                af = start
        sar[i] = s
        trend[i] = float(tr)
        ph2, pl2 = ph1, pl1
        ph1, pl1 = high[i], low[i]
    return sar, trend


def psar_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    sar, trend = psar_arrays(high, low, close, float(p["start"]), float(p["step"]), float(p["max"]))
    return pd.DataFrame({"sar": sar, "trend": trend}, index=frame.index)


def adx_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    from .base import wilder_array

    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    n = int(p["n"])
    m = high.shape[0]
    adx = np.full(m, NaN)
    pdi = np.full(m, NaN)
    mdi = np.full(m, NaN)
    if m < 2:
        return pd.DataFrame({"adx": adx, "plus_di": pdi, "minus_di": mdi}, index=frame.index)
    pdm = np.zeros(m - 1)
    mdm = np.zeros(m - 1)
    tr = np.zeros(m - 1)
    for i in range(1, m):
        up = high[i] - high[i - 1]
        dn = low[i - 1] - low[i]
        pdm[i - 1] = up if (up > dn and up > 0.0) else 0.0
        mdm[i - 1] = dn if (dn > up and dn > 0.0) else 0.0
        tr[i - 1] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    spdm = wilder_array(pdm, n)
    smdm = wilder_array(mdm, n)
    str_ = wilder_array(tr, n)
    dx = np.full(m - 1, NaN)
    for j in range(m - 1):
        if np.isnan(spdm[j]):
            continue
        p_di = 100.0 * spdm[j] / str_[j] if str_[j] > 0.0 else 0.0
        m_di = 100.0 * smdm[j] / str_[j] if str_[j] > 0.0 else 0.0
        pdi[j + 1] = p_di
        mdi[j + 1] = m_di
        dsum = p_di + m_di
        dx[j] = 100.0 * abs(p_di - m_di) / dsum if dsum > 0.0 else 0.0
    valid = ~np.isnan(dx)
    if valid.any():
        start = int(np.argmax(valid))
        adx_vals = wilder_array(dx[start:], n)
        adx[start + 1:] = adx_vals  # dx[j] aligns to bar j+1
    return pd.DataFrame({"adx": adx, "plus_di": pdi, "minus_di": mdi}, index=frame.index)


def supertrend_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    n, mult = int(p["n"]), float(p["mult"])
    atr = _atr_array(high, low, close, n)
    m = high.shape[0]
    line = np.full(m, NaN)
    trend = np.full(m, NaN)
    started = False
    pfu = pfl = pc = None
    prev_in_upper = True
    for i in range(m):
        if np.isnan(atr[i]):
            continue
        mid = (high[i] + low[i]) / 2.0
        bu = mid + mult * atr[i]
        bl = mid - mult * atr[i]
        if not started:
            fu, fl = bu, bl
            ln, in_upper, tr = fu, True, -1
            started = True
        else:
            fu = bu if (bu < pfu or pc > pfu) else pfu
            fl = bl if (bl > pfl or pc < pfl) else pfl
            if prev_in_upper:
                if close[i] > fu:
                    ln, in_upper, tr = fl, False, 1
                else:
                    ln, in_upper, tr = fu, True, -1
            else:
                if close[i] < fl:
                    ln, in_upper, tr = fu, True, -1
                else:
                    ln, in_upper, tr = fl, False, 1
        pfu, pfl, pc = fu, fl, close[i]
        prev_in_upper = in_upper
        line[i] = ln
        trend[i] = float(tr)
    return pd.DataFrame({"line": line, "trend": trend}, index=frame.index)


def cross_strength_batch(frame: pd.DataFrame, p: dict, ctx=None) -> pd.DataFrame:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    ef = ema_array(close, int(p["fast"]))
    es = ema_array(close, int(p["slow"]))
    atr = _atr_array(high, low, close, int(p["atr_n"]))
    out = np.full(close.shape[0], NaN)
    for i in range(close.shape[0]):
        if not np.isnan(ef[i]) and not np.isnan(es[i]) and not np.isnan(atr[i]) and atr[i] > 0.0:
            out[i] = (ef[i] - es[i]) / atr[i]
    return pd.DataFrame({"value": out}, index=frame.index)
