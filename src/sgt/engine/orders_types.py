"""Value types, internal order/symbol state, and intent builders for the order
lifecycle (plan §7.3, §8).

Factored out of :mod:`sgt.engine.orders` so that module can stay focused on the
:class:`~sgt.engine.orders.OrderLifecycle` control flow. This module holds:

- the broker-capability descriptor (:class:`BrokerCaps`) and the action value types
  (:class:`SubmitAction` / :class:`CancelAction`, and the :data:`Action` union) the
  lifecycle emits for its adapter to execute,
- the ``RuntimeFor`` callback alias,
- the private per-order (:class:`_Order`) and per-symbol (:class:`_Sym`) bookkeeping
  records the lifecycle mutates,
- the pure :class:`~sgt.core.types.OrderIntent` builders (entry / exit / protective
  stop, and the two restart-seeding variants) plus the protective-reason predicate.

Everything here is PURE and broker-agnostic: no broker import, no I/O. The public
names (``BrokerCaps``, ``SubmitAction``, ``CancelAction``, ``Action``, ``RuntimeFor``)
are re-exported from :mod:`sgt.engine.orders` so existing
``from sgt.engine.orders import X`` imports keep working.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Union

from sgt.core.types import (
    Bar,
    OrderIntent,
    OrderType,
    Side,
    TimeInForce,
)
from sgt.strategy.base import Decision
from sgt.strategy.runtime import StrategyRuntime

_BPS = 1.0e-4

# Protective exits must actually get out of a position (capital-protection doctrine,
# plan §11.6). In any segment where a broker-side STOP is NOT live (extended hours —
# STOPs are RTH-only — halts, or a broker with no stop support) these are priced as
# marketable limits so they fill on a gapping thin name instead of resting far away.
_PROTECTIVE_REASONS = frozenset({"stop", "stop_loss_pct", "risk_flatten", "session_end_forced"})


def _is_protective(reason: str) -> bool:
    return reason.replace("exit:", "") in _PROTECTIVE_REASONS


@dataclass(frozen=True, slots=True)
class BrokerCaps:
    supports_extended_hours: bool
    supports_broker_side_stops: bool


@dataclass(frozen=True, slots=True)
class SubmitAction:
    intent: OrderIntent


@dataclass(frozen=True, slots=True)
class CancelAction:
    client_order_id: str
    symbol: str
    reason: str = ""


Action = Union[SubmitAction, CancelAction]

RuntimeFor = Callable[[str], StrategyRuntime | None]


@dataclass(slots=True)
class _Order:
    cid: str
    kind: str          # "entry" | "exit" | "stop"
    symbol: str
    qty: int
    reason: str = ""
    filled: int = 0
    terminal: bool = False
    limit_price: float = 0.0   # last submitted limit (exits only; drives re-pricing)

    @property
    def working(self) -> bool:
        return not self.terminal and self.filled < self.qty


@dataclass(slots=True)
class _Sym:
    pos_qty: int = 0
    entry_qty: int = 0
    stop_price: float = 0.0
    entry: _Order | None = None
    stop: _Order | None = None
    exit: _Order | None = None
    entry_age: int = 0


def entry_intent(d: Decision, bar: Bar, cid: str, *, account_id: str, strategy_id: str,
                 ext: bool) -> OrderIntent:
    return OrderIntent(
        client_order_id=cid, account_id=account_id, symbol=d.symbol, side=Side.BUY,
        qty=int(d.qty_hint), order_type=OrderType.LIMIT, limit_price=d.limit_price,
        tif=TimeInForce.DAY, extended_hours=ext, strategy_id=strategy_id,
        reason=d.reason, created_at=bar.ts, stop_price=d.stop_price)


def exit_intent(symbol: str, qty: int, bar: Bar, reason: str, cid: str, *, account_id: str,
                strategy_id: str, ext: bool, exit_offset_bps: float,
                limit: float | None = None) -> OrderIntent:
    price = limit if limit is not None else round(bar.close * (1.0 - exit_offset_bps * _BPS), 6)
    return OrderIntent(
        client_order_id=cid, account_id=account_id, symbol=symbol, side=Side.SELL,
        qty=int(qty), order_type=OrderType.LIMIT, limit_price=price, tif=TimeInForce.DAY,
        extended_hours=ext, strategy_id=strategy_id, reason=reason,
        created_at=bar.ts, stop_price=None)


def stop_intent(symbol: str, qty: int, bar: Bar, stop_price: float, cid: str, *,
                account_id: str, strategy_id: str) -> OrderIntent:
    return OrderIntent(
        client_order_id=cid, account_id=account_id, symbol=symbol, side=Side.SELL,
        qty=int(qty), order_type=OrderType.STOP, limit_price=None, tif=TimeInForce.GTC,
        extended_hours=False, strategy_id=strategy_id, reason="exit:stop",
        created_at=bar.ts, stop_price=stop_price)


def seed_stop_intent(symbol: str, qty: int, now: datetime, stop_price: float, cid: str, *,
                     account_id: str, strategy_id: str) -> OrderIntent:
    return OrderIntent(
        client_order_id=cid, account_id=account_id, symbol=symbol, side=Side.SELL,
        qty=int(qty), order_type=OrderType.STOP, limit_price=None, tif=TimeInForce.GTC,
        extended_hours=False, strategy_id=strategy_id, reason="exit:stop",
        created_at=now, stop_price=stop_price)


def seed_exit_intent(symbol: str, qty: int, now: datetime, price: float, cid: str, *,
                     account_id: str, strategy_id: str, ext: bool,
                     exit_offset_bps: float) -> OrderIntent:
    limit = round(price * (1.0 - exit_offset_bps * _BPS), 6)
    return OrderIntent(
        client_order_id=cid, account_id=account_id, symbol=symbol, side=Side.SELL,
        qty=int(qty), order_type=OrderType.LIMIT, limit_price=limit, tif=TimeInForce.DAY,
        extended_hours=ext, strategy_id=strategy_id, reason="exit:seed_stop",
        created_at=now, stop_price=None)
