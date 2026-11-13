"""The owner's TradingView "Clean Bull Flag Finder (Box Style + Strength)" ported to Python, exactly as frozen in
docs/findings/prereg_bull_flag_finder_2026-11-13.md. Pure and deterministic: given one symbol-session's 1-minute bars
(all stored bars of the day, extended hours included, state starting at the day's first bar) it returns the list of
breakout signals the Pine v5 indicator would have plotted, in bar order.

Pine semantics reproduced literally (the prereg froze them):
- ``ta.atr(14)`` = Wilder RMA of the true range: true range of the first bar is ``high - low``; the first ATR value is
  the SMA of the first 14 true ranges (at 0-based bar 13), then ``atr = (atr[1]*13 + tr)/14``; ``na`` before bar 13.
- ``ta.sma(volume, 20)`` = simple mean of the last 20 volumes; ``na`` before bar 19.
- Per bar ``i`` the cooldown ``cd`` is decremented first (floor 0); the scan runs only when ``cd == 0`` and both ATR
  and the volume SMA at bar ``i`` are defined (``not na``).
- ``flagHigh = ta.highest(high, fb)[1]`` / ``flagLow = ta.lowest(low, fb)[1]`` = the extremes over bars ``i-fb..i-1``
  (the ``[1]`` history shift moves the ``fb``-bar window back one bar). For ``fb = 3..8`` ascending, the breakout is
  ``close_i > flagHigh and volume_i > 1.3 x volSma_i``.
- On a breakout, for ``pb = 3..8`` ascending with ``poleEndOffset = fb + 1``: ``poleHigh/poleLow =
  ta.highest/ta.lowest(.., pb)[fb+1]`` = the extremes over bars ``i-fb-pb..i-fb-1``; ``atrAtPoleEnd = ta.atr(14)[fb+1]``
  and ``volAtPoleEnd = ta.sma(volume, 20)[fb+1]`` are those series at bar ``i-fb-1``.
- ``poleStrong`` = upMove% >= 3.0 and poleRange >= 1.5 x atrAtPoleEnd; ``flagShallow`` = 0.20 <= retrace <= 0.45;
  ``volOK`` = volume_i > 1.3 x volSma_i and volAtPoleEnd > 0. ``na`` in any comparison fails it (Pine ``na`` is falsey),
  so a window that reaches before the day's first bar, or an undefined ATR/SMA, simply does not pass.
- The first ``(fb, pb)`` pair in that nested ascending order that passes is the signal; then ``cd`` is set to 10.

Strength (``showStrength`` block) is clamped to [0, 100]:
``0.6 * upMove% + 25 * (1 - clamp01((retrace - 0.20) / 0.2501)) + 15 * min(3, volume_i / volSma_i)``.

The caller supplies a DataFrame with columns ``ts, open, high, low, close, volume`` in time order; ``ema`` is a helper
(alpha ``2/(n+1)`` seeded with the first close, i.e. ``pandas.ewm(span=n, adjust=False)``) used by the study's
EMA9 > EMA21 gate, not by the detector itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

# Default inputs, frozen (prereg "Indicator (exact port, default inputs)").
MIN_FLAG_BARS, MAX_FLAG_BARS = 3, 8
MIN_POLE_BARS, MAX_POLE_BARS = 3, 8
MIN_POLE_MOVE_PCT = 3.0
POLE_ATR_MULT = 1.5
MIN_RETRACE, MAX_RETRACE = 0.20, 0.45
VOL_SPIKE_MULT = 1.3
COOLDOWN_BARS = 10
ATR_LEN = 14
VOL_LEN = 20
# Pine: maxRetrace - minRetrace + 0.0001 = 0.45 - 0.20 + 0.0001.
_RETRACE_SPAN = (MAX_RETRACE - MIN_RETRACE) + 0.0001
_RANGE_EPS = 0.0001                                 # Pine: poleRange + 0.0001 in the retrace denominator


@dataclass(frozen=True)
class Signal:
    """One breakout the indicator plots. ``i``/``ts`` are the breakout (signal) bar; the flag box spans bars
    ``i-fb..i-1`` and the pole bars ``i-fb-pb..i-fb-1``."""
    i: int
    ts: Any
    fb: int
    pb: int
    flag_high: float
    flag_low: float
    pole_high: float
    pole_low: float
    up_move_pct: float
    retrace: float
    vol_ratio: float
    strength: float


def ema(values: np.ndarray | pd.Series, length: int) -> np.ndarray:
    """EMA with alpha 2/(length+1) seeded at the first value (Pine ``ta.ema``; pandas ``ewm(adjust=False)``)."""
    return pd.Series(np.asarray(values, dtype=float)).ewm(span=length, adjust=False).mean().to_numpy()


def wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = ATR_LEN) -> np.ndarray:
    """Wilder ATR (``ta.atr``): RMA of the true range. ``na`` (NaN) for bars before ``length`` true ranges exist."""
    n = len(high)
    tr = np.empty(n, dtype=float)
    if n:
        tr[0] = high[0] - low[0]                     # ta.tr(true): no prior close -> high - low on the first bar
    if n > 1:
        prev_close = close[:-1]
        tr[1:] = np.maximum.reduce([high[1:] - low[1:],
                                    np.abs(high[1:] - prev_close),
                                    np.abs(low[1:] - prev_close)])
    atr = np.full(n, np.nan, dtype=float)
    if n >= length:
        atr[length - 1] = tr[:length].mean()         # ta.rma seed: SMA of the first `length` true ranges
        for i in range(length, n):
            atr[i] = (atr[i - 1] * (length - 1) + tr[i]) / length
    return atr


def _rolling_max(a: np.ndarray, window: int) -> np.ndarray:
    return pd.Series(a).rolling(window).max().to_numpy()


def _rolling_min(a: np.ndarray, window: int) -> np.ndarray:
    return pd.Series(a).rolling(window).min().to_numpy()


def find_signals(bars: pd.DataFrame) -> list[Signal]:
    """Every signal the indicator fires over ``bars`` (one symbol-session, time-ordered). Deterministic and causal:
    the signal at bar ``i`` depends only on bars ``0..i``."""
    n = len(bars)
    if n == 0:
        return []
    high = bars["high"].to_numpy(dtype=float)
    low = bars["low"].to_numpy(dtype=float)
    close = bars["close"].to_numpy(dtype=float)
    volume = bars["volume"].to_numpy(dtype=float)
    ts = bars["ts"].to_numpy()

    atr = wilder_atr(high, low, close)
    vol_sma = pd.Series(volume).rolling(VOL_LEN).mean().to_numpy()
    # Rolling extremes per window length; value at index j covers bars j-w+1..j. Build highs and lows for 3..8 both.
    hi_max = {w: _rolling_max(high, w) for w in range(MIN_FLAG_BARS, MAX_FLAG_BARS + 1)}
    lo_min = {w: _rolling_min(low, w) for w in range(MIN_FLAG_BARS, MAX_FLAG_BARS + 1)}

    signals: list[Signal] = []
    cd = 0
    thr = VOL_SPIKE_MULT
    for i in range(n):
        cd = max(cd - 1, 0)                           # decremented every bar, before the scan (Pine line 29)
        if cd != 0:
            continue
        atr_i, vs_i = atr[i], vol_sma[i]
        if np.isnan(atr_i) or np.isnan(vs_i):         # scan only when ta.atr and ta.sma are defined at bar i
            continue
        vol_break_i = volume[i] > vs_i * thr          # the breakout volume test (shared by `breakout` and `volOK`)
        found: Signal | None = None
        for fb in range(MIN_FLAG_BARS, MAX_FLAG_BARS + 1):
            fe = i - 1                                # flag end bar (ta.highest(.., fb)[1])
            if i - fb < 0:                            # window reaches before the first bar; larger fb is worse
                break
            flag_high = hi_max[fb][fe]
            flag_low = lo_min[fb][fe]
            if np.isnan(flag_high) or np.isnan(flag_low):
                continue
            if not (close[i] > flag_high and vol_break_i):
                continue
            pe = i - fb - 1                           # pole end bar (offset fb+1)
            if pe < 0:
                continue
            for pb in range(MIN_POLE_BARS, MAX_POLE_BARS + 1):
                if i - fb - pb < 0:                   # pole window reaches before the first bar; larger pb is worse
                    break
                pole_high = hi_max[pb][pe]
                pole_low = lo_min[pb][pe]
                if np.isnan(pole_high) or np.isnan(pole_low):
                    continue
                pole_range = pole_high - pole_low
                up_move_pct = (pole_high - pole_low) / pole_low * 100.0
                retrace = (pole_high - flag_low) / (pole_range + _RANGE_EPS)
                atr_pe, vs_pe = atr[pe], vol_sma[pe]
                pole_strong = up_move_pct >= MIN_POLE_MOVE_PCT and pole_range >= atr_pe * POLE_ATR_MULT
                flag_shallow = MIN_RETRACE <= retrace <= MAX_RETRACE
                vol_ok = vol_break_i and vs_pe > 0
                if pole_strong and flag_shallow and vol_ok:
                    vol_ratio = volume[i] / vs_i
                    found = Signal(i=i, ts=ts[i], fb=fb, pb=pb, flag_high=float(flag_high),
                                   flag_low=float(flag_low), pole_high=float(pole_high), pole_low=float(pole_low),
                                   up_move_pct=float(up_move_pct), retrace=float(retrace),
                                   vol_ratio=float(vol_ratio), strength=_strength(up_move_pct, retrace, vol_ratio))
                    break
            if found is not None:
                break
        if found is not None:
            signals.append(found)
            cd = COOLDOWN_BARS                         # Pine line 79: cd := cooldownBars on a signal
    return signals


def _strength(up_move_pct: float, retrace: float, vol_ratio: float) -> float:
    """Pine strength score, clamped to [0, 100]."""
    retrace_factor = 1.0 - min(1.0, max(0.0, (retrace - MIN_RETRACE) / _RETRACE_SPAN))
    raw = (up_move_pct * 0.6) + (retrace_factor * 25.0) + (min(3.0, vol_ratio) * 15.0)
    return float(min(100.0, max(0.0, raw)))


def strength(up_move_pct: float, retrace: float, vol_ratio: float) -> float:
    """Public alias for the strength score (tests compute it by hand and compare)."""
    return _strength(up_move_pct, retrace, vol_ratio)
