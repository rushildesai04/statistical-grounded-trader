"""Intraday setups on 1-minute bars after a channel alert (long-first rebuild, owner 2026-11-10;
docs/findings/prereg_long_setups_2026-11-10.md). One code path for research and the live engine.

Bars: a DataFrame of RTH 1-minute bars sorted by ``ts`` (bar START, UTC) with open/high/low/close/volume. Minutes with
no trades have no bar (thin names): a gap is not a halt and not a candle. A bar is known only once it has closed
(``ts + 1 min``), so every setup is decided from closed bars and its order can first fill on a LATER bar (``ready``).

Setups (owner's trader: first red candle, bull flag, break of structure; "test all, best wins"; bull flag BOTH ways):
- ``first_red``  L4a/L7a: the first red candle (close < open) after the start -> buy at the next bar's open.
                 L4b/L7b: same candle -> buy-stop at its high (the dip-buy trigger).
                 Stop: the red candle's low. (L7 = the same rules started at a halt reopen.)
- ``bull_flag``  L5: pole (a run-up of at least POLE_MIN_RISE within POLE_LOOKBACK bars, ending at its high), then a
                 flag of FLAG_MIN..FLAG_MAX bars with at least FLAG_MIN_RED red candles, no new high, retracing at most
                 FLAG_MAX_RETRACE of the pole, on lighter volume than the pole -> buy-stop at the flag high, stop at the
                 flag low. S2: the same flag failing -> sell-stop at the flag low, stop at the flag high. The two orders
                 cancel each other: each one's trigger is the other's cancel level.
- ``bos_long``   L6: a bar CLOSES above the last confirmed swing high after a confirmed swing low has formed below it
                 (break of structure) -> buy at the next bar's open, stop at that swing low. Swings use PIVOT_K bars on
                 each side, so a swing is confirmed only PIVOT_K bars after it.
Exits (``simulate``): stop first, then a target at RR x the risk, else the close of the last bar within HOLD of the
entry (or the session's last bar). A stop-entry long fills at max(trigger, bar open); if the same bar also trades at
the stop it is filled AND stopped (conservative); a bar opening through the stop cancels the order.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

BAR = pd.Timedelta(minutes=1)
WATCH = pd.Timedelta(minutes=30)        # a setup must complete within this long after the start bar
TRIGGER_BARS = 5                        # an order lives for this many minutes after ``ready``
HOLD = pd.Timedelta(minutes=30)
RR = 2.0
FLAG_MIN, FLAG_MAX, FLAG_MIN_RED = 2, 4, 2          # owner: "two to three, maybe three to four red candles"
POLE_LOOKBACK, POLE_MIN_RISE, FLAG_MAX_RETRACE = 15, 0.05, 0.5
PIVOT_K = 2


@dataclass(frozen=True)
class Setup:
    kind: str                           # L4a, L4b, L5, L6, L7a, L7b, S2
    side: str                           # "long" | "short"
    ready: pd.Timestamp                 # close of the setup's last bar: the earliest fill
    trigger: float | None               # stop-entry level; None = market at the first bar starting at/after ``ready``
    stop: float
    info: dict = field(default_factory=dict, compare=False)

    @property
    def expires(self) -> pd.Timestamp:
        return self.ready + TRIGGER_BARS * BAR


@dataclass(frozen=True)
class Trade:
    kind: str
    side: str
    entry_ts: pd.Timestamp
    entry: float
    exit_ts: pd.Timestamp
    exit: float
    reason: str                         # stop | target | time
    gross: float                        # long exit/entry - 1; short (entry - exit)/entry


def _window(bars: pd.DataFrame, start: pd.Timestamp) -> pd.DataFrame:
    return bars[(bars.ts >= start) & (bars.ts < start + WATCH)]


def first_red(bars: pd.DataFrame, start: pd.Timestamp, prefix: str = "L4") -> list[Setup]:
    """The first red candle at or after ``start`` within WATCH: market buy (``a``) and buy-stop at its high (``b``)."""
    w = _window(bars, start)
    red = w[w.close < w.open]
    if red.empty:
        return []
    r = red.iloc[0]
    ready, info = r.ts + BAR, {"red_ts": r.ts, "red_high": float(r.high), "red_low": float(r.low)}
    return [Setup(f"{prefix}a", "long", ready, None, float(r.low), info),
            Setup(f"{prefix}b", "long", ready, float(r.high), float(r.low), info)]


def find_flag(bars: pd.DataFrame, start: pd.Timestamp) -> dict | None:
    """The first bull flag that completes on a bar at or after ``start`` within WATCH (shortest flag first)."""
    bars = bars.reset_index(drop=True)
    o, h, lo, c, v = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    first, end = int(bars.ts.searchsorted(start, "left")), int(bars.ts.searchsorted(start + WATCH, "left"))
    for j in range(first, end):
        for n in range(FLAG_MIN, FLAG_MAX + 1):
            p = j - n                                       # pole high bar, just before the flag
            if p < 1:
                break
            q0 = max(0, p - POLE_LOOKBACK + 1)
            p_hi = h[p]
            if p_hi < h[q0:p + 1].max():                    # the pole ends at its own high
                continue
            fl = slice(p + 1, j + 1)
            if h[fl].max() > p_hi or int((c[fl] < o[fl]).sum()) < FLAG_MIN_RED:
                continue
            q = q0 + int(np.argmin(lo[q0:p + 1]))           # pole low
            p_lo = lo[q]
            if p_lo <= 0 or (p_hi - p_lo) / p_lo < POLE_MIN_RISE:
                continue
            f_lo, f_hi = lo[fl].min(), h[fl].max()
            if f_lo < p_hi - FLAG_MAX_RETRACE * (p_hi - p_lo):
                continue
            if v[fl].mean() >= v[q:p + 1].mean():
                continue
            return {"pole_lo": float(p_lo), "pole_hi": float(p_hi), "pole_lo_ts": bars.ts.iloc[q],
                    "pole_hi_ts": bars.ts.iloc[p], "flag_lo": float(f_lo), "flag_hi": float(f_hi),
                    "flag_bars": n, "flag_red": int((c[fl] < o[fl]).sum()), "ready": bars.ts.iloc[j] + BAR}
    return None


def bull_flag(bars: pd.DataFrame, start: pd.Timestamp) -> list[Setup]:
    f = find_flag(bars, start)
    if f is None:
        return []
    return [Setup("L5", "long", f["ready"], f["flag_hi"], f["flag_lo"], f),
            Setup("S2", "short", f["ready"], f["flag_lo"], f["flag_hi"], f)]


def _pivots(h: np.ndarray, lo: np.ndarray, k: int = PIVOT_K) -> tuple[list[int], list[int]]:
    highs = [i for i in range(k, len(h) - k) if h[i] > h[i - k:i].max() and h[i] > h[i + 1:i + k + 1].max()]
    lows = [i for i in range(k, len(lo) - k) if lo[i] < lo[i - k:i].min() and lo[i] < lo[i + 1:i + k + 1].min()]
    return highs, lows


def bos_long(bars: pd.DataFrame, start: pd.Timestamp) -> list[Setup]:
    """First close above the last confirmed swing high that has a confirmed swing low after it (L6)."""
    ctx = bars[(bars.ts >= start - POLE_LOOKBACK * BAR) & (bars.ts < start + WATCH)].reset_index(drop=True)
    if len(ctx) < 2 * PIVOT_K + 2:
        return []
    h, lo, c = (ctx[k].to_numpy(float) for k in ("high", "low", "close"))
    ph, pl = _pivots(h, lo)
    for j in np.flatnonzero((ctx.ts >= start).to_numpy()):
        known_h = [i for i in ph if i + PIVOT_K <= j]       # confirmed by the close of bar i + K, at or before j
        if not known_h:
            continue
        i_h = known_h[-1]
        known_l = [i for i in pl if i_h < i and i + PIVOT_K <= j]
        if not known_l or c[j] <= h[i_h]:
            continue
        i_l = known_l[-1]
        return [Setup("L6", "long", ctx.ts.iloc[j] + BAR, None, float(lo[i_l]),
                      {"swing_high": float(h[i_h]), "swing_low": float(lo[i_l]), "break_ts": ctx.ts.iloc[j]})]
    return []


def simulate(bars: pd.DataFrame, s: Setup, hold: pd.Timedelta = HOLD, rr: float = RR,
             latest: pd.Timestamp | None = None) -> Trade | None:
    """Fill and exit ``s`` on ``bars`` (see module doc). None = no fill (expired, cancelled, or entry beyond the stop).
    ``latest``: no fill on a bar starting at or after it (the 15:45 ET last entry, as for the fade)."""
    after = bars[bars.ts >= s.ready]
    live = after[after.ts < (s.expires if latest is None else min(s.expires, latest))]
    long = s.side == "long"
    entry = fill_i = None
    for i, b in enumerate(live.itertuples()):
        if s.trigger is None:                               # market at the first bar's open
            if (long and b.open <= s.stop) or (not long and b.open >= s.stop):
                return None
            entry, fill_i = float(b.open), i
            break
        if (long and b.open <= s.stop) or (not long and b.open >= s.stop):
            return None                                     # opened through the cancel level
        hit = b.high >= s.trigger if long else b.low <= s.trigger
        if hit:
            entry = max(s.trigger, b.open) if long else min(s.trigger, b.open)
            fill_i = i
            break
        if (long and b.low <= s.stop) or (not long and b.high >= s.stop):
            return None                                     # cancelled before it triggered
    if entry is None or entry <= 0:
        return None
    risk = (entry - s.stop) if long else (s.stop - entry)
    if risk <= 0:
        return None
    target = entry + rr * risk if long else entry - rr * risk
    fill_bar = live.iloc[fill_i]
    entry_ts = fill_bar.ts
    path = after[(after.ts >= entry_ts) & (after.ts < entry_ts + hold)]
    for k, b in enumerate(path.itertuples()):
        first = k == 0
        if long:
            if b.low <= s.stop:                             # same-bar entry and stop: stopped (conservative)
                px = s.stop if first and s.trigger is not None else min(s.stop, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "stop", px / entry - 1)
            if b.high >= target and not (first and s.trigger is not None):
                px = max(target, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "target", px / entry - 1)
        else:
            if b.high >= s.stop:
                px = s.stop if first and s.trigger is not None else max(s.stop, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "stop", (entry - px) / entry)
            if b.low <= target and not (first and s.trigger is not None):
                px = min(target, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "target", (entry - px) / entry)
    last = path.iloc[-1]
    px = float(last.close)
    return Trade(s.kind, s.side, entry_ts, entry, last.ts, px, "time", px / entry - 1 if long else (entry - px) / entry)
