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
Fill bar and target (2026-11-11, owner on GEVO: "$1.96 target vs a $1.975 fill"): a stop-entry's fill bar that reaches
the target traded it AFTER the entry (the bar opened at or below the trigger, so it crossed the trigger first), so the
resting target fills on that bar at the target. Before, the fill bar's target was skipped and a later bar's higher
open was taken (GEVO $1.975), or a later stop (a target the trade had already reached).
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


# Bull flag v2 (owner's grades of 18 charts, 2026-11-10: 6/12 detections were real flags, 1 real flag missed). Each
# change answers a graded failure; v1 above stays frozen for prereg_long_setups_2026-11-10.
V2_MIN_RISE = 0.10          # SLNH "no distinct, strong pole" (+5.3%); the six real flags rose 14-80%
V2_MIN_FILL = 0.90          # NXTT/PFSA/JL "fragmented, large gaps, sparse": pole bars cover >= 90% of its minutes
V2_IMPULSE_BARS = 5         # ATNF missed: the flag is lighter than the push into the high, not the whole pole's average


def find_flag_v2(bars: pd.DataFrame, start: pd.Timestamp) -> dict | None:
    """v1's flag plus: a pole of at least V2_MIN_RISE, trading in at least V2_MIN_FILL of its minutes, ending at a NEW
    HIGH OF DAY (SAGT: "a prolonged recovery after a sharp selloff"), and a flag lighter than the last V2_IMPULSE_BARS
    pole bars. Same no-peeking contract: decided on bars up to the flag's last bar."""
    bars = bars.reset_index(drop=True)
    o, h, lo, c, v = (bars[k].to_numpy(float) for k in ("open", "high", "low", "close", "volume"))
    ts = bars.ts
    first, end = int(ts.searchsorted(start, "left")), int(ts.searchsorted(start + WATCH, "left"))
    for j in range(first, end):
        for n in range(FLAG_MIN, FLAG_MAX + 1):
            p = j - n
            if p < 1:
                break
            q0 = max(0, p - POLE_LOOKBACK + 1)
            p_hi = h[p]
            if p_hi < h[q0:p + 1].max() or (p > 0 and p_hi <= h[:p].max()):     # pole top = a new high of day
                continue
            fl = slice(p + 1, j + 1)
            if h[fl].max() > p_hi or int((c[fl] < o[fl]).sum()) < FLAG_MIN_RED:
                continue
            q = q0 + int(np.argmin(lo[q0:p + 1]))
            p_lo = lo[q]
            if p_lo <= 0 or (p_hi - p_lo) / p_lo < V2_MIN_RISE:
                continue
            span = (ts.iloc[p] - ts.iloc[q]).total_seconds() / 60 + 1
            if (p - q + 1) / span < V2_MIN_FILL:
                continue
            f_lo, f_hi = lo[fl].min(), h[fl].max()
            if f_lo < p_hi - FLAG_MAX_RETRACE * (p_hi - p_lo):
                continue
            if v[fl].mean() >= v[max(q, p - V2_IMPULSE_BARS + 1):p + 1].mean():
                continue
            return {"pole_lo": float(p_lo), "pole_hi": float(p_hi), "pole_lo_ts": ts.iloc[q], "pole_hi_ts": ts.iloc[p],
                    "flag_lo": float(f_lo), "flag_hi": float(f_hi), "flag_bars": n,
                    "flag_red": int((c[fl] < o[fl]).sum()), "ready": ts.iloc[j] + BAR}
    return None


def bull_flag_v2(bars: pd.DataFrame, start: pd.Timestamp) -> list[Setup]:
    f = find_flag_v2(bars, start)
    if f is None:
        return []
    return [Setup("L5v2", "long", f["ready"], f["flag_hi"], f["flag_lo"], f),
            Setup("S2v2", "short", f["ready"], f["flag_lo"], f["flag_hi"], f)]


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


# 2026-11-19 (owner: "explore the problem and find a fix ... add on top of the complexity and cases for the LONG
# book"). The one-minute setups above buy within minutes of the alert with a stop at a single candle's low: on the
# official tape 45% of those trades are stopped in the entry minute and 15.6% win. Waiting until the stock has built
# a BASE (some minutes in a narrow range) and then closes above it, above the session's average price, with the stop
# under the whole base, was stopped in the entry minute 1% of the time and won 35% / 30% (training / held-out) of its
# trades; per trade it lost -0.86% / -1.30% where the one-minute setups lost -2.07% / -2.32%. Still not a winner after
# costs (about break-even before them): it is the better-built rule, run at test size until live trades decide.
BASE_MIN = pd.Timedelta(minutes=10)         # the base: this long before the break, all of it after the alert's start bar
BASE_MIN_BARS = 5                           # bars that must exist in it (a thin feed has no bar for a quiet minute)
# 2026-11-19 night (docs/findings/long_bot_replay_2026-11-19.md): 0.08 until then. The live book sees the base on the
# free feed, which carries about 1% of the real volume and so shows every base NARROWER than it is on the full tape
# (November: 12 of 31 live-style bases were wider than 8% on the tape and lost -2.8%; the 19 real ones -1.2%). The
# whole bot replayed on 3,146 past alerts and on November: a cap of 4% as the feed shows it loses less than 8% in all
# three samples (-0.69% / -0.83% / -0.19% a trade against -0.89% / -1.01% / -1.85%), and the trades it drops are the
# worse ones. A stop-gap for a thin feed, not an edge: no width makes the entry profitable, on the feed or the tape.
BASE_MAX_RANGE = 0.04                       # (highest high - lowest low) / lowest low of the base, as the feed shows it
BASE_WATCH = pd.Timedelta(minutes=60)       # the break must come within this long after the start bar


def session_vwap(bars: pd.DataFrame) -> np.ndarray:
    """The running volume-weighted price of ``bars`` (typical price x volume), known at each bar's close."""
    v = bars.volume.to_numpy(float)
    cv = np.cumsum(v)
    typ = ((bars.high + bars.low + bars.close) / 3).to_numpy(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cv > 0, np.cumsum(typ * v) / cv, np.nan)


def base_break(bars: pd.DataFrame, start: pd.Timestamp) -> list[Setup]:
    """L8: the first bar, BASE_MIN to BASE_WATCH after ``start``, that CLOSES above the high of the base (the bars of
    the BASE_MIN before it: at least BASE_MIN_BARS of them, in a range of at most BASE_MAX_RANGE) and above the
    session VWAP of ``bars`` -> buy at the next bar's open, stop at the base's low. Decided from closed bars only."""
    if bars.empty:
        return []
    ts, h, lo, c = bars.ts, bars.high.to_numpy(float), bars.low.to_numpy(float), bars.close.to_numpy(float)
    vw = session_vwap(bars)
    for j in np.flatnonzero(((ts >= start + BASE_MIN) & (ts <= start + BASE_WATCH)).to_numpy()):
        a = int(ts.searchsorted(ts.iloc[j] - BASE_MIN, side="left"))
        if j - a < BASE_MIN_BARS:                           # (every bar of it is at/after ``start``: j is BASE_MIN past it)
            continue
        hi_w, lo_w = float(h[a:j].max()), float(lo[a:j].min())
        if lo_w > 0 and (hi_w - lo_w) / lo_w <= BASE_MAX_RANGE and c[j] > hi_w and vw[j] == vw[j] and c[j] > vw[j]:
            return [Setup("L8", "long", ts.iloc[j] + BAR, None, lo_w,
                          {"base_high": hi_w, "base_low": lo_w, "break_ts": ts.iloc[j], "vwap": float(vw[j])})]
    return []


def _exit_exact(path: pd.DataFrame, after: pd.DataFrame, s: Setup, entry: float, entry_ts: pd.Timestamp, rr: float,
                long: bool, red_exit: bool) -> Trade:
    """The exit walk of ``simulate`` under ``price-decimal-v1``: the same order of checks, the same booked prices,
    with the stop and target comparisons made on exact decimals and the target computed exactly. Kept apart from the
    registered walk so that not one line of that walk changes."""
    from decimal import Decimal

    from sgt.fast import exact_price as xp

    stop, e = xp.price(s.stop), xp.price(entry)
    rate = Decimal(repr(float(rr)))                       # the registered reward-to-risk (2.0) as an exact number
    target = xp.long_target(e, stop, rate) if long else e - rate * (stop - e)
    tgt = float(target)
    for k, b in enumerate(path.itertuples()):
        first = k == 0
        if long:
            if xp.at_or_below(b.low, stop):
                px = s.stop if first and s.trigger is not None else min(s.stop, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "stop", px / entry - 1)
            if xp.at_or_above(b.high, target):
                px = tgt if first else max(tgt, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "target", px / entry - 1)
            if red_exit and first and b.close < entry:
                nxt = after[after.ts > b.ts]
                if len(nxt):
                    px = float(nxt.iloc[0].open)
                    return Trade(s.kind, s.side, entry_ts, entry, nxt.iloc[0].ts, px, "red", px / entry - 1)
                px = float(b.close)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "red", px / entry - 1)
        else:
            if xp.at_or_above(b.high, stop):
                px = s.stop if first and s.trigger is not None else max(s.stop, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "stop", (entry - px) / entry)
            if xp.at_or_below(b.low, target):
                px = tgt if first else min(tgt, b.open)
                return Trade(s.kind, s.side, entry_ts, entry, b.ts, px, "target", (entry - px) / entry)
    last = path.iloc[-1]
    px = float(last.close)
    return Trade(s.kind, s.side, entry_ts, entry, last.ts, px, "time", px / entry - 1 if long else (entry - px) / entry)


def simulate(bars: pd.DataFrame, s: Setup, hold: pd.Timedelta = HOLD, rr: float = RR,
             latest: pd.Timestamp | None = None, *, red_exit: bool = False, exact: bool = False) -> Trade | None:
    """Fill and exit ``s`` on ``bars`` (see module doc). None = no fill (expired, cancelled, or entry beyond the stop).
    ``latest``: no fill on a bar starting at or after it (the 15:45 ET last entry, as for the fade).
    ``red_exit`` (longs; prereg_long_risk_2026-11-12 E, adopted for the live long book): a fill bar that neither stops
    nor reaches the target and CLOSES below the entry sells at the next bar's open (the next bar of ``bars``, even past
    a halt; none left: that close), reason "red".

    ``exact`` (2026-11-25, amendment_stop_arithmetic_2026-11-25, option A): False is the REGISTERED arithmetic
    (``binary-v0``) and is what every registered record uses; with the default nothing here differs from before
    (tests/fixtures/patterns_simulate_v0.py is the frozen copy it is checked against). True is ``price-decimal-v1``:
    the exit's stop and target comparisons are exact decimal ones (``sgt.fast.exact_price``), the target is computed
    exactly and never rounded. The fill is the same in both (it compares reported prices directly). True is used
    only for the parallel figure stored beside a settled trade; it raises ``exact_price.NotAPrice`` when a value is
    not a reported price, and the caller records that as a data-quality outcome."""
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
    if exact:
        return _exit_exact(path, after, s, entry, entry_ts, rr, long, red_exit)
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
