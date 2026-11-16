"""Estimated costs of a LIVE alert trade (2026-11-16), so a live estimate is quoted after costs like its official result.

Until the 20:40 ET settle a trade is known only by the listener's prices. Every text and page showed that estimate
BEFORE costs, so a day read better live than it settled (review 2026-11-14: Friday read -$100 live and settled lower).
The official result charges the gapper round-trip spread at the entry (``GAPPER_COST_MODEL`` with its time-of-day
multipliers) plus a locate fee on shorts; the same model is applied here to the live entry time and price. Pure: no
network, no database. The official number still replaces the estimate at the settle (official prices, same model).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.decision.features import CostModelAdapter

LOCATE = 0.005                                     # shorts: the locate fee the official fade result charges
_MODEL = CostModelAdapter(GAPPER_COST_MODEL, tag="gapper", open_hour_mult=GAPPER_OPEN_HOUR_MULT,
                          rest_mult=GAPPER_REST_MULT)


def _when(at) -> datetime | None:
    if isinstance(at, datetime):
        ts = at
    elif isinstance(at, str) and at:
        try:
            ts = datetime.fromisoformat(at)
        except ValueError:
            return None
    else:
        return None
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def cost(side: str, symbol: str | None, at, price) -> float | None:
    """Estimated cost of the whole trade as a fraction of the entry: round-trip spread at (entry time, entry price),
    plus the locate fee for a short. None when there is no positive entry price (no estimate can be quoted)."""
    try:
        px = float(price)
    except (TypeError, ValueError):
        return None
    if not px > 0:
        return None
    ts = _when(at)
    rt = _MODEL.round_trip(symbol or "", ts, px)[0]
    if ts is None:                                 # no usable entry time: the widest spread of the day, never the cheapest
        rt *= max(GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT)
    return rt + (LOCATE if side == "short" else 0.0)


def net(side: str, symbol: str | None, at, entry, exit_px) -> float | None:
    """Live estimate of a trade's return AFTER estimated costs; None without both prices."""
    c = cost(side, symbol, at, entry)
    try:
        a, b = float(entry), float(exit_px)
    except (TypeError, ValueError):
        return None
    if c is None or not b > 0:
        return None
    return ((a - b) / a if side == "short" else b / a - 1) - c
