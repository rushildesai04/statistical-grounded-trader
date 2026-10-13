"""Factories shared by the broker tests (bars and order intents)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sgt.core.types import Bar, OrderIntent, OrderType, Segment, Side, TimeInForce

UTC = timezone.utc
SESSION = date(2026, 10, 12)

# 09:30 ET == 13:30 UTC on 2026-10-12 (EDT). Bars offset from the RTH open by minute.
_RTH_OPEN_UTC = datetime(2026, 10, 12, 13, 30, tzinfo=UTC)


def make_bar(symbol="AAA", *, minute=570, open=5.0, high=None, low=None, close=5.0,
             volume=100_000.0, segment=Segment.RTH, session_date=SESSION) -> Bar:
    ts = _RTH_OPEN_UTC + timedelta(minutes=minute - 570)
    hi = max(open, close) if high is None else high
    lo = min(open, close) if low is None else low
    hi = max(hi, open, close)
    lo = min(lo, open, close)
    return Bar(symbol=symbol, ts=ts, open=open, high=hi, low=lo, close=close,
               volume=volume, session_date=session_date, minute_of_day=minute, segment=segment)


def make_intent(*, cid="cid-1", account_id="acct", symbol="AAA", side=Side.BUY, qty=10,
                order_type=OrderType.LIMIT, limit_price=5.5, tif=TimeInForce.DAY,
                extended_hours=False, reason="entry", stop_price=None) -> OrderIntent:
    return OrderIntent(
        client_order_id=cid, account_id=account_id, symbol=symbol, side=side, qty=qty,
        order_type=order_type, limit_price=limit_price, tif=tif,
        extended_hours=extended_hours, strategy_id="strat", reason=reason,
        created_at=datetime(2026, 10, 12, 14, 30, tzinfo=UTC), stop_price=stop_price,
    )
