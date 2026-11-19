"""The trader's trend matrix, live (owner 2026-11-18: "take the trader's advice to heart").

The trader reads six conditions on the 1, 2, 3 and 5 minute charts: MACD (12/31/10) above its signal, close above
EMA 34, RSI 14 above 50, close above the Bollinger middle (20), Supertrend (10, 3) up, price-volume trend above its
EMA 15. His rule: "As long as the 1M shows all green, stay in the trade ... MACD going red is usually a sign to
start exiting." The settings are his; the code is written here from the standard definitions of the indicators.

What this module does: the matrix as one line for the long texts and cards, and the moment his rule would leave a
long the book holds (MACD turning red after it has been green since the entry). It is shown and recorded on every
live long; it does not move a book. Tested on history (`prereg_trend_matrix_exit_2026-11-18`,
`prereg_trend_book_2026-11-18`): applied to every alert it does not pay, and it does stay with the rare runner;
the live record is there to learn where a person would use it.

Live candles are the free feed's (thin on small stocks), so a condition can differ from his chart.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

NAMES = ("macd", "ema", "rsi", "bb", "supertrend", "pvt")
LABELS = {"macd": "MACD", "ema": "EMA", "rsi": "RSI", "bb": "Bollinger", "supertrend": "Supertrend", "pvt": "PVT"}
FRAMES = (1, 2, 3, 5)
MACD_FAST, MACD_SLOW, MACD_SIG, EMA_N, RSI_N, BB_N, ST_N, ST_MULT, PVT_N = 12, 31, 10, 34, 14, 20, 10, 3.0, 15
MIN_BARS = 5                              # fewer candles than this on a chart: no reading for that chart
BAR = pd.Timedelta(minutes=1)


def supertrend_up(h: np.ndarray, lo: np.ndarray, c: np.ndarray) -> np.ndarray:
    """True while the supertrend is up: bands at close -/+ ST_MULT x the ST_N-candle average true range; the lower
    band only rises while the close stays above it, the upper only falls while the close stays below it; the trend
    turns down on a close under the previous lower band and up on a close over the previous upper band."""
    n = len(c)
    prev = np.r_[c[0], c[:-1]] if n else c
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev), np.abs(lo - prev)))
    atr = pd.Series(tr).rolling(ST_N).mean().to_numpy()
    up, dn, trend = np.full(n, np.nan), np.full(n, np.nan), np.ones(n, int)
    for i in range(n):
        if not atr[i] == atr[i]:
            trend[i] = trend[i - 1] if i else 1
            continue
        u, d = c[i] - ST_MULT * atr[i], c[i] + ST_MULT * atr[i]
        u1, d1 = (up[i - 1], dn[i - 1]) if i and up[i - 1] == up[i - 1] else (u, d)
        up[i] = max(u, u1) if i and c[i - 1] > u1 else u
        dn[i] = min(d, d1) if i and c[i - 1] < d1 else d
        t = trend[i - 1] if i else 1
        trend[i] = 1 if (t == -1 and c[i] > d1) else -1 if (t == 1 and c[i] < u1) else t
    return trend == 1


def conditions(bars: pd.DataFrame) -> pd.DataFrame:
    """The six conditions (True = green) at each candle's close, over time-ordered candles."""
    c, v = bars.close.astype(float).reset_index(drop=True), bars.volume.astype(float).reset_index(drop=True)
    ema = lambda x, n: x.ewm(span=n, adjust=False).mean()  # noqa: E731
    macd = ema(c, MACD_FAST) - ema(c, MACD_SLOW)
    d = c.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / RSI_N, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / RSI_N, adjust=False).mean()
    rsi = (100 - 100 / (1 + gain / loss.replace(0, np.nan))).fillna(100)
    pvt = (c.pct_change().fillna(0) * v).cumsum()
    return pd.DataFrame({"macd": macd > ema(macd, MACD_SIG), "ema": c > ema(c, EMA_N), "rsi": rsi > 50,
                         "bb": c > c.rolling(BB_N, min_periods=1).mean(),
                         "supertrend": supertrend_up(bars.high.to_numpy(float), bars.low.to_numpy(float), c.to_numpy()),
                         "pvt": pvt > ema(pvt, PVT_N)})


def closed(bars: pd.DataFrame, at: datetime) -> pd.DataFrame:
    """Today's 1-minute candles that have closed by ``at``, time-ordered (bounded: one day of minutes)."""
    if bars is None or bars.empty:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    f = bars[bars.ts + BAR <= pd.Timestamp(at)]
    return f.sort_values("ts").tail(1_000).reset_index(drop=True)


def resample(bars: pd.DataFrame, n: int) -> pd.DataFrame:
    """Completed ``n``-minute candles (clock-aligned) from closed 1-minute ones; the bucket still forming is left out."""
    if n == 1 or bars.empty:
        return bars
    bucket = bars.ts.dt.floor(f"{n}min")
    g = bars.assign(bucket=bucket).groupby("bucket", sort=True)
    out = pd.DataFrame({"ts": g.ts.first(), "open": g.open.first(), "high": g.high.max(), "low": g.low.min(),
                        "close": g.close.last(), "volume": g.volume.sum()}).reset_index(drop=True)
    last = bars.ts.iloc[-1]
    done = out.ts.dt.floor(f"{n}min") + pd.Timedelta(minutes=n) <= last + BAR      # its last minute has closed
    return out[done.to_numpy()].reset_index(drop=True)


def snapshot(bars: pd.DataFrame, at: datetime) -> dict | None:
    """The matrix as of ``at``: per chart (1, 2, 3, 5 minutes) the six conditions, or None for a chart with too few
    candles. None when there is nothing to read at all."""
    one = closed(bars, at)
    if len(one) < MIN_BARS:
        return None
    out = {}
    for n in FRAMES:
        f = resample(one, n)
        out[n] = None if len(f) < MIN_BARS else {k: bool(v) for k, v in conditions(f).iloc[-1].items()}
    return out if out.get(1) else None


def line(snap: dict | None) -> str | None:
    """One line for a text: how many of the six are green on each chart, and what is red on the 1-minute one."""
    if not snap or not snap.get(1):
        return None
    parts = []
    for n in FRAMES:
        col = snap.get(n)
        if col:
            parts.append(f"{n}M {sum(col.values())}/6")
    red = [LABELS[k] for k in NAMES if not snap[1][k]]
    tail = "1M all green: his rule says stay in" if not red else "1M red: " + ", ".join(red)
    return "📐 Trader's matrix " + " · ".join(parts) + " · " + tail


def going_red(bars: pd.DataFrame, entry_ts: pd.Timestamp, at: datetime) -> dict | None:
    """Where his rule leaves a long entered on the candle starting ``entry_ts``: the first close at which MACD is
    red AFTER a close at which it was green since the entry ("MACD going red"). Price: the next candle's open when
    it has closed too, else that close. None while that has not happened."""
    one = closed(bars, at)
    if len(one) < MIN_BARS:
        return None
    macd = conditions(one).macd.to_numpy(bool)
    ts, o, c = one.ts, one.open.to_numpy(float), one.close.to_numpy(float)
    seen = False
    for j in np.flatnonzero((ts >= entry_ts).to_numpy()):
        if macd[j]:
            seen = True
        elif seen:
            nxt = j + 1 < len(one)
            return {"px": float(o[j + 1] if nxt else c[j]), "at": (ts.iloc[j + 1] if nxt else ts.iloc[j] + BAR), "why": "MACD turned red"}
    return None


def last_price(bars: pd.DataFrame, at: datetime) -> float | None:
    one = closed(bars, at)
    return float(one.close.iloc[-1]) if len(one) else None
