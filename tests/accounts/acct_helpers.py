"""Helpers for account-layer tests: sim adapters seeded with positions."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sgt.brokers import CostModel, SimBroker
from sgt.core.types import Bar, OrderType, Segment, Side, TimeInForce
from sgt.core.types import OrderIntent

UTC = timezone.utc
SESSION = date(2026, 10, 12)
_OPEN = datetime(2026, 10, 12, 13, 30, tzinfo=UTC)
ZERO = CostModel(hs_under_1=0, hs_1_to_5=0, hs_5_to_20=0, hs_over_20=0)


def bar(symbol="AAA", *, minute=570, price=5.0, volume=10_000_000.0) -> Bar:
    ts = _OPEN + timedelta(minutes=minute - 570)
    return Bar(symbol=symbol, ts=ts, open=price, high=price, low=price, close=price,
               volume=volume, session_date=SESSION, minute_of_day=minute, segment=Segment.RTH)


def seeded_sim(account_id: str, symbol: str, qty: int, price: float = 5.0) -> SimBroker:
    """A SimBroker holding ``qty`` shares of ``symbol`` at ``price``."""
    sim = SimBroker(account_id, ZERO, 10_000_000.0)
    if qty > 0:
        intent = OrderIntent(
            client_order_id=f"seed-{account_id}-{symbol}", account_id=account_id,
            symbol=symbol, side=Side.BUY, qty=qty, order_type=OrderType.MARKET,
            limit_price=None, tif=TimeInForce.DAY, extended_hours=False,
            strategy_id="seed", reason="seed", created_at=_OPEN)
        sim.submit_sync(intent)
        sim.on_bar(bar(symbol, minute=570, price=price, volume=max(1e7, qty * 1000)))
        assert sim._positions[symbol].qty == qty, sim._positions[symbol].qty
    return sim
