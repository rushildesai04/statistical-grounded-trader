"""Monthly passive rebalance planner (60/40 SPY/IEF by default), 2026-10-29.

Pure planning: given the sleeve's holdings, cash and prices, return the orders that bring the
sleeve back to its target weights. Only the target symbols are managed; any other holding in
the account is ignored (never sold). Whole shares, sells before buys, a cash buffer, and a
drift threshold so small deviations do not churn the account.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from sgt.intraday.calendar import default_calendar

DEFAULT_TARGETS = {"SPY": 0.60, "IEF": 0.40}


@dataclass(frozen=True, slots=True)
class PlannedOrder:
    symbol: str
    side: str              # "buy" | "sell"
    qty: int
    limit: float
    notional: float


def first_trading_day(d: date) -> bool:
    """True iff ``d`` is the first exchange session of its calendar month."""
    cal = default_calendar()
    if not cal.is_session(d):
        return False
    return not any(cal.is_session(date(d.year, d.month, k)) for k in range(1, d.day))


def plan(*, cash: float, holdings: dict[str, int], prices: dict[str, float],
         targets: dict[str, float] = DEFAULT_TARGETS, cash_buffer: float = 0.01,
         drift_threshold: float = 0.01, limit_band: float = 0.003,
         max_order_notional: float | None = None) -> list[PlannedOrder]:
    if abs(sum(targets.values()) - 1.0) > 1e-9 or any(w < 0 for w in targets.values()):
        raise ValueError("targets must be non-negative and sum to 1")
    for s in targets:
        if not (prices.get(s) or 0) > 0:
            raise ValueError(f"missing/invalid price for {s}")
    sleeve = cash + sum(holdings.get(s, 0) * prices[s] for s in targets)
    if sleeve <= 0:
        raise ValueError("sleeve equity must be positive")
    investable = sleeve * (1.0 - cash_buffer)
    orders: list[PlannedOrder] = []
    for s, w in targets.items():
        px, cur = prices[s], holdings.get(s, 0)
        cur_w = cur * px / sleeve
        if abs(cur_w - w) < drift_threshold and cur > 0:
            continue
        delta = (w * investable - cur * px) / px
        qty = int(math.floor(abs(delta)))
        if qty == 0:
            continue
        side = "buy" if delta > 0 else "sell"
        limit = round(px * (1 + limit_band) if side == "buy" else px * (1 - limit_band), 2)
        orders.append(PlannedOrder(s, side, qty, limit, qty * px))
    orders.sort(key=lambda o: o.side != "sell")           # sells first (fund the buys)
    # buys must fit the cash available after sells, minus the buffer
    avail = cash + sum(o.notional for o in orders if o.side == "sell") - sleeve * cash_buffer
    out = []
    for o in orders:
        if o.side == "buy":
            cost = o.qty * o.limit
            if cost > avail:
                q = int(math.floor(max(avail, 0) / o.limit))
                if q == 0:
                    continue
                o = PlannedOrder(o.symbol, "buy", q, o.limit, q * prices[o.symbol])
                cost = q * o.limit
            avail -= cost
        if max_order_notional is not None and o.notional > max_order_notional:
            raise ValueError(f"order {o.symbol} {o.side} ${o.notional:,.0f} exceeds the "
                             f"max_order_notional ${max_order_notional:,.0f}; refusing")
        out.append(o)
    return out
