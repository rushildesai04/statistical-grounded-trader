"""Execution model v1: what a real order would have got (docs/findings/exec_model_2026-11-21.md, frozen there).

The original record prices a trade from 1-minute bars: a short at the open of the minute after alert + 10 s, a stop at
exactly its level, the spread as an estimated cost. This module re-prices a trade from the official tape's timestamped
trades and best-bid / best-ask quotes, from the moment the bot itself decided:
- a marketable order arrives ``latency`` after the decision and fills at the standing quote (sell at the bid, buy at
  the ask), so the spread is paid in the fills;
- a stop triggers at the first trade through its level and fills at the quote ``latency`` later, never better than the
  level; a long's target is a resting limit filled at its price at the first trade at or above it; the order of stop
  and target is the order of the trades;
- when no usable quote exists or nothing trades in the minute after the order (a halt), the fill is the next trade.

Pure: the caller supplies the tape (``Tape``). It never touches the ops database and changes no stored result. Every
number it returns is an ESTIMATE under stated assumptions (latency, top-of-book size, last-sale stop trigger).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd

from sgt.fast import patterns as pt
from sgt.fast.est_costs import LOCATE
from sgt.fast.fade_rule import HOLD as SHORT_HOLD
from sgt.fast.fade_rule import STOP

LATENCY = pd.Timedelta(seconds=1)            # decision -> order live at the exchange (the baseline)
STRESS_LATENCY = pd.Timedelta(seconds=5)     # stress scenario 1
QUIET = pd.Timedelta(seconds=60)             # no trade this long after an order: treated as halted, fill at next trade
BEFORE_CLOSE = pd.Timedelta(seconds=60)      # a timed exit is never later than this before the session's close
MINUTE = pd.Timedelta(minutes=1)
LONG_HOLD, RR = pd.Timedelta(pt.HOLD), pt.RR
WIDE = 0.10                                  # (ask - bid) / mid above this at a fill -> flag "wide"

QuoteAt = Callable[[str, pd.Timestamp], "dict | None"]     # last quote at or before T: bid, ask, bid_size, ask_size
TradesIn = Callable[[str, pd.Timestamp, pd.Timestamp], "np.ndarray | None"]   # rows (ns, price, size), start <= t < end


class NoData(Exception):
    """A request for quotes or trades failed: the trade cannot be priced (retried by the caller another day)."""


@dataclass(frozen=True, slots=True)
class Tape:
    quote: QuoteAt
    trades: TradesIn
    bars: pd.DataFrame            # the session's official regular-hours 1-minute bars: ts (UTC), open, high, low, close
    close: pd.Timestamp           # the session's regular close


@dataclass(frozen=True, slots=True)
class Fill:
    px: float
    at: pd.Timestamp
    how: str                      # "quote" (the standing bid/ask), "trade" (waited for a trade), "last" (no trade left)
    spread: float | None          # (ask - bid) / mid at a quote fill
    flags: tuple[str, ...]


def _trades(tape: Tape, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> np.ndarray:
    tr = tape.trades(symbol, start, end)
    if tr is None:
        raise NoData(f"trades {symbol}")
    return tr[(tr[:, 0] >= start.value) & (tr[:, 0] < end.value)] if len(tr) else tr


def _ts(ns: float) -> pd.Timestamp:
    return pd.Timestamp(int(ns), tz="UTC")


def fill(tape: Tape, symbol: str, side: str, at: pd.Timestamp, shares: float | None = None) -> Fill:
    """A marketable order live at ``at``: ``side`` "sell" fills at the bid, "buy" at the ask. See the module doc for
    the case with no usable quote or no trade in the next QUIET."""
    q = tape.quote(symbol, at)
    nxt = _trades(tape, symbol, at, at + QUIET)
    bid, ask = (float(q.get("bid") or 0), float(q.get("ask") or 0)) if q else (0.0, 0.0)
    if len(nxt) and bid > 0 and ask >= bid:
        size = float(q.get("bid_size" if side == "sell" else "ask_size") or 0)
        spread = (ask - bid) / ((ask + bid) / 2)
        flags = (("wide",) if spread > WIDE else ()) + (("thin",) if shares and size < shares else ())
        return Fill(bid if side == "sell" else ask, at, "quote", spread, flags)
    if len(nxt):                                               # trading, but no usable quote: the next trade
        return Fill(float(nxt[0, 1]), _ts(nxt[0, 0]), "trade", None, ("halt",))
    b = tape.bars[tape.bars.ts >= at.floor("min")]
    for m in b.ts.iloc[:5]:                                    # the next minutes that have official trades
        tr = _trades(tape, symbol, max(at, m), m + MINUTE)
        if len(tr):
            return Fill(float(tr[0, 1]), _ts(tr[0, 0]), "trade", None, ("halt",))
    if len(tape.bars):                                         # nothing traded again: the session's last price
        return Fill(float(tape.bars.close.iloc[-1]), tape.bars.ts.iloc[-1], "last", None, ("halt", "unresolved"))
    raise NoData(f"no bars {symbol}")


def first_touch(tape: Tape, symbol: str, start: pd.Timestamp, end: pd.Timestamp, lo: float | None = None,
                hi: float | None = None) -> tuple[pd.Timestamp, float, str, float] | None:
    """The first official trade in (start, end) at or below ``lo`` or at or above ``hi``: (time, price, "lo" | "hi",
    that minute's bar extreme on the touched side). Bars pick the minutes; the trades of a minute decide the order."""
    b = tape.bars[(tape.bars.ts >= start.floor("min")) & (tape.bars.ts < end)]
    for r in b.itertuples(index=False):
        if not ((lo is not None and r.low <= lo) or (hi is not None and r.high >= hi)):
            continue
        for t, p, _ in _trades(tape, symbol, max(start, r.ts), min(end, r.ts + MINUTE)):
            if t <= start.value:              # strictly after the entry (spec section 3)
                continue
            if lo is not None and p <= lo:
                return _ts(t), float(p), "lo", float(r.low)
            if hi is not None and p >= hi:
                return _ts(t), float(p), "hi", float(r.high)
    return None


def _out(entry: Fill, px: float, at: pd.Timestamp, how: str, reason: str, gross: float, cost: float,
         flags: tuple[str, ...], stress: float | None = None) -> dict:
    return {"status": "ok", "entry_at": entry.at, "entry": entry.px, "entry_how": entry.how, "spread_in": entry.spread,
            "exit_at": at, "exit": px, "exit_how": how, "reason": reason, "gross": gross, "net": gross - cost,
            "stress_extreme_net": stress, "flags": tuple(dict.fromkeys(flags))}


def short(tape: Tape, symbol: str, decided: pd.Timestamp, *, latency: pd.Timedelta = LATENCY,
          shares: float | None = None) -> dict:
    """The alert fade from the bot's decision: sell short at the bid, 20% buy stop, cover after 60 minutes (or just
    before the close). ``stress_extreme_net``: a stop filled at its minute's high (stress scenario 2)."""
    try:
        e = fill(tape, symbol, "sell", decided + latency, shares)
        end = min(e.at + SHORT_HOLD, tape.close - BEFORE_CLOSE)
        if e.how == "last" or not e.px > 0 or e.at >= end:
            return {"status": "unresolved", "why": "no trade after the entry order", "flags": e.flags}
        level = e.px * (1 + STOP)
        hit = first_touch(tape, symbol, e.at, end, hi=level)
        if hit is not None:
            x = fill(tape, symbol, "buy", hit[0] + latency, shares)
            px = max(x.px, level)
            worst = max(px, hit[3])
            return _out(e, px, x.at, x.how, "stop", (e.px - px) / e.px, LOCATE, e.flags + x.flags,
                        (e.px - worst) / e.px - LOCATE)
        x = fill(tape, symbol, "buy", end + latency, shares)
        return _out(e, x.px, x.at, x.how, "60 min" if end == e.at + SHORT_HOLD else "close", (e.px - x.px) / e.px,
                    LOCATE, e.flags + x.flags)
    except NoData as exc:
        return {"status": "no_data", "why": str(exc), "flags": ("no_data",)}


def long(tape: Tape, symbol: str, decided: pd.Timestamp, stop: float, *, red_exit: bool = False,
         latency: pd.Timedelta = LATENCY, shares: float | None = None) -> dict:
    """An alert long from the moment the bot booked it: buy at the ask, sell stop at ``stop``, limit target at 2x the
    risk, else out after 30 minutes; ``red_exit``: the rules-"A2" exit after a red entry minute.
    ``stress_extreme_net``: a stop filled at its minute's low (stress scenario 2, the audit's candle-low figure)."""
    try:
        e = fill(tape, symbol, "buy", decided + latency, shares)
        end = min(e.at + LONG_HOLD, tape.close - BEFORE_CLOSE)
        if e.how == "last" or not e.px > 0 or e.at >= end:
            return {"status": "unresolved", "why": "no trade after the entry order", "flags": e.flags}
        if not e.px > stop:
            return {"status": "no_trade", "why": "the ask was at or under the stop", "entry": e.px, "flags": e.flags}
        target = e.px + RR * (e.px - stop)
        hit = first_touch(tape, symbol, e.at, end, lo=stop, hi=target)
        first_end = min(e.at.floor("min") + MINUTE, end)
        if red_exit and (hit is None or hit[0] >= first_end) and first_end < end:
            tr = _trades(tape, symbol, e.at, first_end)
            if len(tr) and float(tr[-1, 1]) < e.px:
                x = fill(tape, symbol, "sell", first_end + latency, shares)
                return _out(e, x.px, x.at, x.how, "red", x.px / e.px - 1, 0.0, e.flags + x.flags)
        if hit is not None and hit[2] == "lo":
            x = fill(tape, symbol, "sell", hit[0] + latency, shares)
            px = min(x.px, stop)
            return _out(e, px, x.at, x.how, "stop", px / e.px - 1, 0.0, e.flags + x.flags, min(px, hit[3]) / e.px - 1)
        if hit is not None:
            return _out(e, target, hit[0], "limit", "target", target / e.px - 1, 0.0, e.flags)
        x = fill(tape, symbol, "sell", end + latency, shares)
        return _out(e, x.px, x.at, x.how, "time", x.px / e.px - 1, 0.0, e.flags + x.flags)
    except NoData as exc:
        return {"status": "no_data", "why": str(exc), "flags": ("no_data",)}


def ssr(day: pd.DataFrame, at: pd.Timestamp, prior_close: float | None, prior_low: float | None,
        prior_prior_close: float | None) -> bool | None:
    """Was the short-sale price test in force at ``at``? True when the stock traded 10% or more below the prior close
    earlier that day, or did so on the prior session (the restriction carries over one day). ``day``: that day's
    official REGULAR-HOURS bars (the listing market reads the trigger from regular-hours trades; amendment 1 of the
    spec). None = the prior closes are unknown."""
    if not prior_close or prior_close <= 0:
        return None
    before = day[day.ts + MINUTE <= at]       # minutes COMPLETED before the order (amendment 2: no look into its own minute)
    today = bool(len(before) and float(before.low.min()) <= 0.9 * prior_close)
    carried = bool(prior_low and prior_prior_close and prior_low <= 0.9 * prior_prior_close)
    return today or carried
