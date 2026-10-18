"""Pure (I/O-free) mapping between core types and Schwab Trader API JSON.

Kept separate from the adapter so the request/response translation is unit-testable
and the adapter module stays focused on transport and the idempotency protocol.
All functions here are deterministic given their arguments (the caller supplies
``now`` and the segment resolver); none perform I/O.

[VERIFY] the Schwab field names and enum strings used below against the official
developer docs before trading live (see ``sgt.brokers.schwab`` for the full list).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from sgt.core.types import (
    AccountState,
    Fill,
    OrderAck,
    OrderIntent,
    OrderStatus,
    OrderType,
    Position,
    Segment,
    Side,
    TimeInForce,
)

_ET = ZoneInfo("America/New_York")
PRICE_EPS = 1e-4  # tolerance when matching a LIMIT order's price

_DURATION = {
    TimeInForce.DAY: "DAY",
    TimeInForce.GTC: "GOOD_TILL_CANCEL",
    # IOC = fill what you can immediately, cancel the remainder (partial fills allowed).
    # Schwab's duration enum includes IMMEDIATE_OR_CANCEL for exactly this; FILL_OR_KILL is
    # all-or-none and would silently change IOC semantics, so it must not be used here.
    # [VERIFY] the enum spelling "IMMEDIATE_OR_CANCEL" against the Schwab Trader API docs.
    TimeInForce.IOC: "IMMEDIATE_OR_CANCEL",
}

STATUS_MAP = {
    "AWAITING_PARENT_ORDER": OrderStatus.PENDING,
    "AWAITING_CONDITION": OrderStatus.PENDING,
    "AWAITING_MANUAL_REVIEW": OrderStatus.PENDING,
    "ACCEPTED": OrderStatus.WORKING,
    "AWAITING_UR_OUT": OrderStatus.WORKING,
    "PENDING_ACTIVATION": OrderStatus.PENDING,
    "QUEUED": OrderStatus.WORKING,
    "WORKING": OrderStatus.WORKING,
    "REJECTED": OrderStatus.REJECTED,
    "PENDING_CANCEL": OrderStatus.WORKING,
    "CANCELED": OrderStatus.CANCELED,
    "PENDING_REPLACE": OrderStatus.WORKING,
    "REPLACED": OrderStatus.CANCELED,
    "FILLED": OrderStatus.FILLED,
    "EXPIRED": OrderStatus.EXPIRED,
    "NEW": OrderStatus.PENDING,
    "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
}

OPEN_STATUSES = {OrderStatus.PENDING, OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED}


def segment_for(now_utc: datetime) -> Segment | None:
    """US-equity session segment for an instant, or None when the market is closed."""
    et = now_utc.astimezone(_ET)
    mins = et.hour * 60 + et.minute
    if 4 * 60 <= mins < 9 * 60 + 30:
        return Segment.PRE
    if 9 * 60 + 30 <= mins < 16 * 60:
        return Segment.RTH
    if 16 * 60 <= mins < 20 * 60:
        return Segment.POST
    return None


def parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def fmt_entered(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def intent_to_json(intent: OrderIntent) -> dict[str, Any]:
    return {
        "client_order_id": intent.client_order_id,
        "symbol": intent.symbol,
        "side": intent.side.value,
        "qty": intent.qty,
        "order_type": intent.order_type.value,
        "limit_price": intent.limit_price,
        "stop_price": intent.stop_price,
        "tif": intent.tif.value,
        "extended_hours": intent.extended_hours,
        "strategy_id": intent.strategy_id,
        "reason": intent.reason,
        "created_at": intent.created_at.isoformat(),
    }


def session_for(intent: OrderIntent, now: datetime,
                segment_fn: Callable[[datetime], Segment | None]) -> str:
    """Map an intent to a Schwab order ``session``.

    Regular orders trade NORMAL. Extended-hours limit orders map to the current
    segment (PRE -> AM, POST -> PM). When extended hours is requested but the segment
    can't be determined (market closed / RTH), we use SEAMLESS so the limit order
    stays eligible across pre/regular/post rather than silently trading one window.
    """
    if not intent.extended_hours:
        return "NORMAL"
    segment = segment_fn(now)
    if segment is Segment.PRE:
        return "AM"
    if segment is Segment.POST:
        return "PM"
    return "SEAMLESS"


def order_body(intent: OrderIntent, session: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "orderType": intent.order_type.value.upper(),
        "session": session,
        "duration": _DURATION[intent.tif],
        "orderStrategyType": "SINGLE",
        "orderLegCollection": [{
            "instruction": intent.side.value.upper(),
            "quantity": intent.qty,
            "instrument": {"symbol": intent.symbol, "assetType": "EQUITY"},
        }],
    }
    if intent.order_type is OrderType.LIMIT and intent.limit_price is not None:
        body["price"] = intent.limit_price
    if intent.order_type is OrderType.STOP and intent.stop_price is not None:
        body["stopPrice"] = intent.stop_price
    return body


def order_matches(order: dict[str, Any], intent: OrderIntent) -> bool:
    """Whether a broker order is the same SINGLE-leg order described by ``intent``."""
    legs = order.get("orderLegCollection") or []
    if len(legs) != 1:
        return False
    leg = legs[0]
    if str(leg.get("instruction", "")).upper() != intent.side.value.upper():
        return False
    if str(leg.get("instrument", {}).get("symbol", "")) != intent.symbol:
        return False
    if int(float(leg.get("quantity", 0))) != intent.qty:
        return False
    if intent.order_type is OrderType.LIMIT and intent.limit_price is not None:
        price = order.get("price")
        if price is None or abs(float(price) - intent.limit_price) > PRICE_EPS:
            return False
    return True


def ack_from_order(cid: str, order: dict[str, Any], now: datetime) -> OrderAck:
    status = STATUS_MAP.get(str(order.get("status", "")).upper(), OrderStatus.PENDING)
    legs = order.get("orderLegCollection") or []
    side: Side | None = None
    symbol = ""
    quantity = 0
    if legs:
        instruction = str(legs[0].get("instruction", "")).upper()
        side = Side.SELL if instruction.startswith("SELL") else Side.BUY if instruction else None
        symbol = str(legs[0].get("instrument", {}).get("symbol", ""))
        quantity = int(float(legs[0].get("quantity", 0)))
    remaining = order.get("remainingQuantity")
    remaining_qty = int(float(remaining)) if remaining is not None else (
        quantity - int(float(order.get("filledQuantity", 0) or 0)) if legs else None)
    ts_raw = order.get("enteredTime")
    ts = parse_ts(str(ts_raw)) if ts_raw else now
    return OrderAck(
        client_order_id=cid,
        broker_order_id=str(order.get("orderId", "")),
        status=status,
        ts=ts,
        message=str(order.get("status", "")),
        symbol=symbol,
        side=side,
        remaining_qty=remaining_qty,
    )


def execution_legs(order: dict[str, Any]) -> list[dict[str, Any]]:
    legs: list[dict[str, Any]] = []
    for activity in order.get("orderActivityCollection") or []:
        legs.extend(activity.get("executionLegs") or [])
    return legs


def account_state_from(account_id: str, payload: dict[str, Any]) -> AccountState:
    """Map a Schwab ``securitiesAccount`` payload to a long-only ``AccountState``."""
    acct = payload.get("securitiesAccount", {}) or {}
    balances = acct.get("currentBalances", {}) or {}
    positions = tuple(
        Position(
            symbol=str(p["instrument"]["symbol"]),
            qty=int(float(p.get("longQuantity", 0))),
            avg_price=float(p.get("averagePrice", 0.0)),
        )
        for p in acct.get("positions", [])
        if float(p.get("longQuantity", 0)) > 0
    )
    cash = float(balances.get("cashBalance", 0.0))
    return AccountState(
        account_id=account_id,
        cash=cash,
        settled_cash=float(balances.get("cashAvailableForTrading", cash)),
        buying_power=float(balances.get("buyingPower", 0.0)),
        positions=positions,
    )


def working_ack(intent: OrderIntent, broker_order_id: str, now: datetime,
                message: str) -> OrderAck:
    """A WORKING ack for a just-submitted (or already-submitted) order."""
    return OrderAck(
        client_order_id=intent.client_order_id,
        broker_order_id=broker_order_id,
        status=OrderStatus.WORKING,
        ts=now,
        message=message,
        symbol=intent.symbol,
        side=intent.side,
        remaining_qty=intent.qty,
    )


def anchor_from_stored(stored: dict[str, Any], fallback: datetime) -> datetime:
    """created_at of the ORIGINAL intent for crash-restart reconciliation, or ``fallback``."""
    raw = stored.get("created_at")
    if raw:
        try:
            return parse_ts(str(raw))
        except (ValueError, TypeError):
            pass
    return fallback


def reconcile_window(anchor: datetime, now: datetime, slack_min: int) -> dict[str, str]:
    """Order-history query window anchored on the intent (``anchor``), not on ``now``."""
    return {
        "fromEnteredTime": fmt_entered(anchor - timedelta(minutes=slack_min)),
        "toEnteredTime": fmt_entered(now + timedelta(minutes=1)),
    }


def fills_from_order(order: dict[str, Any], account_id: str, cid: str) -> list[Fill]:
    """One ``Fill`` per execution leg of a Schwab order (caller dedupes on ``fill_id``)."""
    order_id = str(order.get("orderId", ""))
    if not order_id:
        return []
    legs = order.get("orderLegCollection") or []
    instruction = str(legs[0].get("instruction", "BUY")).upper() if legs else "BUY"
    side = Side.SELL if instruction.startswith("SELL") else Side.BUY
    symbol = str(legs[0].get("instrument", {}).get("symbol", "")) if legs else ""
    out: list[Fill] = []
    for idx, leg in enumerate(execution_legs(order)):
        out.append(Fill(
            client_order_id=cid,
            broker_order_id=order_id,
            account_id=account_id,
            symbol=symbol,
            side=side,
            qty=int(float(leg["quantity"])),
            price=float(leg["price"]),
            ts=parse_ts(str(leg["time"])),
            fill_id=f"{order_id}-{leg.get('legId', idx)}",
        ))
    return out
