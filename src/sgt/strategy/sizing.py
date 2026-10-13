"""Position sizing (plan §7.2 ``sizing``).

Fixed-risk sizing: risk a fixed fraction of the strategy's allocation on the
distance between entry and the protective stop, then cap by the max notional. The
result is a whole-share count; anything that rounds below one share is a skip
(``0``), never a fractional or zero-risk order.

Meta-filter size-by-score (plan §9.1): when enabled, scale the base quantity by the
calibrated take-probability ``score`` in [0, 1]:

    qty = floor(base_qty * score)

Because ``score <= 1`` this can only ever REDUCE size, never exceed the fixed-risk
base. A scaled size below one share is a skip.
"""
from __future__ import annotations

import math

from sgt.strategy.schema import Sizing


def fixed_risk_qty(sizing: Sizing, entry_price: float, stop_price: float) -> int:
    """Whole shares to buy under fixed-risk sizing, or 0 to skip."""
    if entry_price <= 0 or stop_price <= 0 or entry_price <= stop_price:
        return 0
    per_share_risk = entry_price - stop_price
    risk_budget = sizing.allocation_usd * (sizing.risk_per_trade_pct_of_allocation / 100.0)
    qty = math.floor(risk_budget / per_share_risk)
    notional_cap = math.floor(sizing.max_notional_usd / entry_price)
    qty = min(qty, notional_cap)
    return qty if qty >= 1 else 0


def size_for_decision(
    sizing: Sizing, entry_price: float, stop_price: float,
    *, score: float | None = None, size_by_score: bool = False,
) -> int:
    """Final share count: fixed-risk base, optionally scaled by meta-filter score."""
    base = fixed_risk_qty(sizing, entry_price, stop_price)
    if base <= 0:
        return 0
    if not size_by_score or score is None:
        return base
    if not 0.0 <= score <= 1.0:
        raise ValueError(f"meta-filter score must be in [0, 1], got {score}")
    scaled = math.floor(base * score)
    return scaled if scaled >= 1 else 0
