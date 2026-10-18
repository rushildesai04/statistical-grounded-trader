"""Opening-range breakout on in-play days (H3, 2026-10-18): a point-in-time event study.

Hypothesis (documented for "stocks in play": gap days with heavy opening volume): when the
first ``or_minutes`` regular-session bars close green and price later CLOSES above their
high, go long at the next bar's open, stop at the opening-range low, and hold to the close.
The hold is hours, not minutes, so the spread (0.5-0.9% round trip on gappers) is small
relative to the move it targets -- the failure mode of the 1-minute bracket labels.

Everything is decided from information available at the time:
* the opening range is complete at ``rth_open + or_minutes``;
* the opening relative volume divides the range's volume by the mean volume of the same
  opening window over PRIOR sessions only;
* entry fills at the open of the bar after the signal bar, never the signal close;
* a bar touching the stop exits at the stop, or at its open if it gapped through it;
* costs come from a :class:`~sgt.decision.features.CostSource` at entry and exit time
  (per-side half of the round trip).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from sgt.core.types import Bar, Segment
from sgt.decision.features import CostSource
from sgt.intraday.calendar import default_calendar

RTH_OPEN_MINUTE = 570   # 09:30 ET


@dataclass(frozen=True, slots=True)
class ORBConfig:
    or_minutes: int = 5
    require_green_or: bool = True
    min_open_rel_vol: float | None = None   # opening-window volume / prior-session mean
    price_min: float = 1.0
    exit_minutes_before_close: int = 5
    max_entry_minute: int = 15 * 60          # no new entries after 15:00 ET


@dataclass(frozen=True, slots=True)
class ORBTrade:
    symbol: str
    session: date
    entry_ts: object
    entry_price: float      # raw fill (bar open)
    exit_price: float       # raw fill
    stop: float
    net_return: float       # after both half-spreads
    gross_return: float
    exit_reason: str        # "stop" | "close"
    open_rel_vol: float | None


def opening_window(day: Sequence[Bar], or_minutes: int) -> list[Bar]:
    return [b for b in day if b.segment is Segment.RTH
            and RTH_OPEN_MINUTE <= b.minute_of_day < RTH_OPEN_MINUTE + or_minutes]


def open_rel_vol(day: Sequence[Bar], prior_days: Sequence[Sequence[Bar]],
                 or_minutes: int) -> float | None:
    """Opening-window volume relative to the mean of the same window on PRIOR sessions."""
    prior = [sum(b.volume for b in opening_window(d, or_minutes)) for d in prior_days]
    prior = [v for v in prior if v > 0]
    if not prior:
        return None
    today = sum(b.volume for b in opening_window(day, or_minutes))
    return today / (sum(prior) / len(prior))


def orb_trade(day: Sequence[Bar], prior_days: Sequence[Sequence[Bar]], cfg: ORBConfig,
              cost: CostSource) -> ORBTrade | None:
    """Simulate at most one long ORB trade on one session's bars (time-ordered)."""
    rth = [b for b in day if b.segment is Segment.RTH]
    window = opening_window(rth, cfg.or_minutes)
    if len(window) < cfg.or_minutes:
        return None                                  # incomplete opening range
    or_high = max(b.high for b in window)
    or_low = min(b.low for b in window)
    if cfg.require_green_or and not window[-1].close > window[0].open:
        return None
    rv = open_rel_vol(day, prior_days, cfg.or_minutes)
    if cfg.min_open_rel_vol is not None and (rv is None or rv < cfg.min_open_rel_vol):
        return None

    close_minute = _rth_close_minute(rth[0])
    cutoff = close_minute - cfg.exit_minutes_before_close
    after = [b for b in rth if b.minute_of_day >= RTH_OPEN_MINUTE + cfg.or_minutes]
    for i, b in enumerate(after[:-1]):
        if b.minute_of_day > cfg.max_entry_minute or b.minute_of_day >= cutoff:
            return None
        if b.close <= or_high:
            continue
        entry_bar = after[i + 1]
        entry = entry_bar.open
        if entry < cfg.price_min or entry <= or_low:
            return None                               # gapped back below the stop
        return _resolve(day[0].symbol, after[i + 1:], entry_bar, entry, or_low, cutoff,
                        rv, cost)
    return None


def _resolve(symbol, bars, entry_bar, entry, stop, cutoff, rv, cost) -> ORBTrade:
    exit_price, reason, exit_bar = None, "close", bars[-1]
    for b in bars:
        if b.low <= stop:
            exit_price, reason, exit_bar = min(stop, b.open), "stop", b
            break
        if b.minute_of_day >= cutoff:
            exit_price, exit_bar = b.close, b
            break
    if exit_price is None:
        exit_price = bars[-1].close
    half_in = 0.5 * cost.round_trip(symbol, entry_bar.ts, entry)[0]
    half_out = 0.5 * cost.round_trip(symbol, exit_bar.ts, exit_price)[0]
    gross = exit_price / entry - 1.0
    net = (exit_price * (1.0 - half_out)) / (entry * (1.0 + half_in)) - 1.0
    return ORBTrade(symbol, entry_bar.session_date, entry_bar.ts, entry, exit_price, stop,
                    net, gross, reason, rv)


def _rth_close_minute(bar: Bar) -> int:
    from sgt.intraday.calendar import ET

    b = default_calendar().bounds(bar.session_date)
    t = b.rth_close.astimezone(ET)
    return t.hour * 60 + t.minute
