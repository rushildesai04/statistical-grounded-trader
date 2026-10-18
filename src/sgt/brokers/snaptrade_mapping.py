"""Pure JSON -> core-type mapping for SnapTrade responses (no I/O, no state).

Kept apart from ``snaptrade.py`` (adapter/orchestration) so the field-name translation is
unit-testable in isolation and the adapter module stays within the size budget. Every field
name here is confirmed against the official SDK typed models (see the [VERIFY] block in
``snaptrade.py``). Quantities arrive as strings; amounts may be int or float.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sgt.core.types import (
    AccountState,
    OrderAck,
    OrderIntent,
    OrderStatus,
    OrderType,
    Position,
    Side,
    TimeInForce,
)

log = logging.getLogger(__name__)

_MATCH_WINDOW_S = 600  # recent-order reconciliation window around the intent anchor

# SnapTrade AccountOrderRecordStatus -> our OrderStatus (type/account_order_record_status.py).
STATUS_MAP: dict[str, OrderStatus] = {
    "NONE": OrderStatus.PENDING,
    "PENDING": OrderStatus.PENDING,
    "PENDING_RISK_REVIEW": OrderStatus.PENDING,
    "ACCEPTED": OrderStatus.WORKING,
    "QUEUED": OrderStatus.WORKING,
    "ACTIVATED": OrderStatus.WORKING,
    "TRIGGERED": OrderStatus.WORKING,
    "CONTINGENT_ORDER": OrderStatus.WORKING,
    "REPLACE_PENDING": OrderStatus.WORKING,
    "CANCEL_PENDING": OrderStatus.WORKING,
    "SUSPENDED": OrderStatus.WORKING,
    "STOPPED": OrderStatus.WORKING,
    "PARTIAL": OrderStatus.PARTIALLY_FILLED,
    "EXECUTED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELED,
    "PARTIAL_CANCELED": OrderStatus.CANCELED,
    "REPLACED": OrderStatus.CANCELED,
    "REJECTED": OrderStatus.REJECTED,
    "FAILED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}
ACTION_MAP = {Side.BUY: "BUY", Side.SELL: "SELL"}
ORDER_TYPE_MAP = {OrderType.LIMIT: "Limit", OrderType.MARKET: "Market", OrderType.STOP: "Stop"}
TIF_MAP = {TimeInForce.DAY: "Day", TimeInForce.GTC: "GTC", TimeInForce.IOC: "IOC"}


def parse_ts(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def num(raw: Any) -> float:
    if raw is None:
        return 0.0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def order_symbol(rec: dict[str, Any]) -> str:
    """Ticker for an AccountOrderRecord, accepting both shapes SnapTrade emits.

    Trade-enabled brokerages return ``universal_symbol.symbol``; some return the nested
    position-style ``symbol.symbol.symbol``. Returns "" when neither is present so the
    caller treats the record as a non-match (reconciliation must never guess a symbol).
    """
    us = rec.get("universal_symbol")
    if isinstance(us, dict):
        sym = us.get("symbol")
        if sym:
            return str(sym)
    sym_wrap = rec.get("symbol")
    if isinstance(sym_wrap, dict):
        inner = sym_wrap.get("symbol")
        if isinstance(inner, dict):
            nested = inner.get("symbol")
            if nested:
                return str(nested)
        elif inner:
            return str(inner)
    return ""


def order_side(rec: dict[str, Any]) -> Side | None:
    action = str(rec.get("action", "")).upper()
    if action.startswith("BUY"):
        return Side.BUY
    if action.startswith("SELL"):
        return Side.SELL
    return None


def positions_from_json(raw: list[dict[str, Any]]) -> tuple[Position, ...]:
    """Map SnapTrade positions to long-only core ``Position`` tuples (whole shares)."""
    out: list[Position] = []
    for p in raw:
        sym_wrap = p.get("symbol") or {}
        inner = sym_wrap.get("symbol") if isinstance(sym_wrap, dict) else None
        symbol = str(inner.get("symbol", "")) if isinstance(inner, dict) else ""
        if not symbol:
            continue
        qty = int(num(p.get("units")))
        if qty == 0:
            continue
        avg = num(p.get("average_purchase_price")) or num(p.get("price"))
        out.append(Position(symbol=symbol.upper(), qty=qty, avg_price=round(avg, 6)))
    return tuple(out)


def place_body(intent: OrderIntent, st_account_id: str) -> dict[str, Any]:
    """SnapTrade ``/trade/place`` body for an intent (RTH only; fail-closed sessions)."""
    body: dict[str, Any] = {
        "account_id": st_account_id,
        "action": ACTION_MAP[intent.side],
        "order_type": ORDER_TYPE_MAP[intent.order_type],
        "time_in_force": TIF_MAP[intent.tif],
        "symbol": intent.symbol,
        "universal_symbol_id": None,
        "trading_session": "REGULAR",
        "units": intent.qty,
        "client_order_id": intent.client_order_id,
    }
    if intent.limit_price is not None:
        body["price"] = intent.limit_price
    if intent.stop_price is not None:
        body["stop"] = intent.stop_price
    return body


def ack_from_record(rec: dict[str, Any], cid: str) -> OrderAck:
    status = STATUS_MAP.get(str(rec.get("status", "")).upper(), OrderStatus.PENDING)
    total = num(rec.get("total_quantity"))
    filled = num(rec.get("filled_quantity"))
    canceled = num(rec.get("canceled_quantity"))
    open_q = rec.get("open_quantity")
    remaining = int(num(open_q)) if open_q is not None else int(round(total - filled - canceled))
    return OrderAck(
        client_order_id=cid,
        broker_order_id=str(rec.get("brokerage_order_id", "")),
        status=status,
        ts=parse_ts(rec.get("time_placed") or rec.get("time_updated")),
        message=str(rec.get("status", "")),
        symbol=order_symbol(rec),
        side=order_side(rec),
        remaining_qty=max(remaining, 0),
    )


def ack_from_ledger(intent: OrderIntent, row: dict[str, Any]) -> OrderAck:
    try:
        status = OrderStatus(row["status"])
    except ValueError:
        status = OrderStatus.PENDING
    return OrderAck(
        client_order_id=intent.client_order_id,
        broker_order_id=row["broker_order_id"],
        status=status,
        ts=datetime.now(timezone.utc),
        message="idempotent replay",
        symbol=intent.symbol,
        side=intent.side,
        remaining_qty=None,
    )


def anchor_from_stored(row: dict[str, Any], fallback: datetime) -> datetime:
    """created_at of the ORIGINAL intent (crash-restart reconciliation), or ``fallback``."""
    raw = (row.get("intent") or {}).get("created_at")
    if raw:
        try:
            return parse_ts(str(raw))
        except (ValueError, TypeError):
            pass
    return fallback


def order_matches(rec: dict[str, Any], intent: OrderIntent, anchor: datetime) -> bool:
    """Whether an AccountOrderRecord is the order described by ``intent`` near ``anchor``."""
    sym = order_symbol(rec)
    if not sym:
        log.warning("snaptrade reconcile: order %s has no recognizable symbol field; "
                    "treating as non-match", rec.get("brokerage_order_id"))
        return False
    if sym.upper() != intent.symbol.upper():
        return False
    if order_side(rec) is not intent.side:
        return False
    if int(num(rec.get("total_quantity"))) != intent.qty:
        return False
    if intent.order_type is OrderType.LIMIT and intent.limit_price is not None:
        if abs(num(rec.get("limit_price")) - intent.limit_price) > 1e-6:
            return False
    placed = parse_ts(rec.get("time_placed"))
    return abs((placed - anchor).total_seconds()) <= _MATCH_WINDOW_S


def account_state_from(account_id: str, balances: list[dict[str, Any]],
                       positions: list[dict[str, Any]]) -> AccountState:
    """Build an ``AccountState`` from SnapTrade balances + positions.

    Balances are per-currency; we sum USD-denominated cash/buying_power. SnapTrade does not
    expose a settled-cash figure, so ``settled_cash`` mirrors ``cash`` (conservative).
    """
    cash = 0.0
    buying_power = 0.0
    for b in balances:
        cur = b.get("currency") or {}
        code = str(cur.get("code", "")).upper() if isinstance(cur, dict) else ""
        if code and code != "USD":
            continue
        cash += num(b.get("cash"))
        bp = b.get("buying_power")
        buying_power += num(bp) if bp is not None else num(b.get("cash"))
    return AccountState(
        account_id=account_id,
        cash=round(cash, 2),
        settled_cash=round(cash, 2),
        buying_power=round(buying_power, 2),
        positions=positions_from_json(positions),
    )
