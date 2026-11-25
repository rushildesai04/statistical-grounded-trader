"""FROZEN COPY of ``sgt.fast.patterns.simulate`` as registered (binary-v0), taken from main at c12aec4 on 2026-11-25,
before the opt-in ``exact`` mode was added. A reference for tests only: the live function with its default
arguments must return exactly what this returns, on any input. Never edited, never imported by the application."""
from __future__ import annotations

import pandas as pd

from sgt.fast.patterns import HOLD, RR, Setup, Trade


def simulate(bars: pd.DataFrame, s: Setup, hold: pd.Timedelta = HOLD, rr: float = RR,
             latest: pd.Timestamp | None = None, *, red_exit: bool = False) -> Trade | None:
    """Fill and exit ``s`` on ``bars`` (see module doc). None = no fill (expired, cancelled, or entry beyond the stop).
    ``latest``: no fill on a bar starting at or after it (the 15:45 ET last entry, as for the fade).
    ``red_exit`` (longs; prereg_long_risk_2026-11-12 E, adopted for the live long book): a fill bar that neither stops
    nor reaches the target and CLOSES below the entry sells at the next bar's open (the next bar of ``bars``, even past
    a halt; none left: that close), reason "red"."""
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
            if b.high >= target:                            # fill bar: traded after the entry (module doc)
                px = target if first else max(target, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "target", px / entry - 1)
            if red_exit and first and b.close < entry:
                nxt = after[after.ts > b.ts]
                if len(nxt):
                    px = float(nxt.iloc[0].open)
                    return Trade(s.kind, s.side, entry_ts, entry, nxt.iloc[0].ts, px, "red", px / entry - 1)
                px = float(b.close)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "red", px / entry - 1)
        else:
            if b.high >= s.stop:
                px = s.stop if first and s.trigger is not None else max(s.stop, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "stop", (entry - px) / entry)
            if b.low <= target:
                px = target if first else min(target, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "target", (entry - px) / entry)
    last = path.iloc[-1]
    px = float(last.close)
    return Trade(s.kind, s.side, entry_ts, entry, last.ts, px, "time", px / entry - 1 if long else (entry - px) / entry)
