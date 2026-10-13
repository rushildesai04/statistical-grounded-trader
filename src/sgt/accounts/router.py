"""Split one approved trade across enabled accounts and bound aggregate exposure.

Two invariants (docs/IMPLEMENTATION_PLAN.md §13.3):
- Connecting more accounts never multiplies exposure on one symbol: an AGGREGATE
  per-symbol notional cap across ALL accounts (existing positions + open orders + the
  new order) is enforced before the entry is split.
- Exits sell from each account's *actual* position and never oversell it.

Allocation is deterministic: entries split by ``allocation_weight`` using the
largest-remainder method on integer shares; client order ids come from
``sgt.core.ids.client_order_id`` with a per-leg index.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime

from sgt.accounts.registry import AccountSpec
from sgt.core.ids import client_order_id
from sgt.core.interfaces import BrokerAdapter
from sgt.core.types import OrderIntent, OrderType, Side, TimeInForce


def _largest_remainder(total: int, weights: Sequence[float]) -> list[int]:
    """Apportion ``total`` whole units across ``weights`` (largest-remainder)."""
    wsum = sum(weights)
    if wsum <= 0 or total <= 0:
        return [0] * len(weights)
    exact = [total * w / wsum for w in weights]
    floors = [int(x) for x in exact]
    remainder = total - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: (exact[i] - floors[i], weights[i]), reverse=True)
    for i in order[:remainder]:
        floors[i] += 1
    return floors


class AccountRouter:
    def __init__(
        self,
        accounts: Sequence[AccountSpec],
        adapters: Mapping[str, BrokerAdapter],
        *,
        max_symbol_notional_total: float,
    ) -> None:
        if max_symbol_notional_total < 0:
            raise ValueError("max_symbol_notional_total must be >= 0")
        self.accounts = list(accounts)
        self.adapters = dict(adapters)
        self.max_symbol_notional_total = max_symbol_notional_total

    def _enabled(self) -> list[AccountSpec]:
        return [a for a in self.accounts
                if a.enabled and a.allocation_weight > 0 and a.account_id in self.adapters]

    async def _positions(self, symbol: str) -> dict[str, int]:
        """Current held shares of ``symbol`` per enabled account."""
        out: dict[str, int] = {}
        for acct in self._enabled():
            state = await self.adapters[acct.account_id].account_state()
            held = next((p.qty for p in state.positions if p.symbol == symbol), 0)
            out[acct.account_id] = held
        return out

    async def _pending_buys(self, symbol: str) -> dict[str, int]:
        """Working (unfilled) BUY shares of ``symbol`` per enabled account.

        Derived from each adapter's open_orders(): working BUY legs add to exposure
        just like held shares, so the aggregate cap counts them even before they fill.
        """
        out: dict[str, int] = {}
        for acct in self._enabled():
            acks = await self.adapters[acct.account_id].open_orders()
            total = sum(
                (a.remaining_qty or 0) for a in acks
                if a.symbol == symbol and a.side is Side.BUY and not a.status.terminal
            )
            out[acct.account_id] = total
        return out

    async def allocate(
        self,
        strategy_id: str,
        symbol: str,
        side: Side,
        total_qty: int,
        limit_price: float | None,
        signal_ts: datetime,
        reason: str,
        extended_hours: bool,
        *,
        pending_shares: Mapping[str, int] | None = None,
    ) -> list[OrderIntent]:
        """Return per-account order intents for one approved trade.

        ``pending_shares`` overrides the working (unfilled) BUY share count per account
        for ``symbol`` that is otherwise derived from each adapter's open_orders(); pass
        it only when the caller's ledger is a more authoritative source. Missing accounts
        are treated as zero. Open orders count toward the aggregate cap either way.
        """
        if total_qty <= 0:
            raise ValueError("total_qty must be > 0")
        if extended_hours and limit_price is None:
            raise ValueError("extended-hours orders must be LIMIT (limit_price required)")
        enabled = self._enabled()
        if not enabled:
            return []
        held = await self._positions(symbol)
        if pending_shares is not None:
            pending = dict(pending_shares)          # explicit override
        elif side is Side.BUY:
            pending = await self._pending_buys(symbol)  # derive from open_orders()
        else:
            pending = {}

        if side is Side.SELL:
            return self._allocate_exit(strategy_id, symbol, total_qty, limit_price,
                                       signal_ts, reason, extended_hours, enabled, held)
        return self._allocate_entry(strategy_id, symbol, total_qty, limit_price,
                                    signal_ts, reason, extended_hours, enabled, held, pending)

    # ------------------------------------------------------------------ entry
    def _allocate_entry(self, strategy_id, symbol, total_qty, limit_price, signal_ts,
                        reason, extended_hours, enabled, held, pending) -> list[OrderIntent]:
        price = limit_price
        # Aggregate per-symbol cap across ALL accounts.
        if price is not None and price > 0:
            existing_shares = sum(held.get(a.account_id, 0) + pending.get(a.account_id, 0)
                                  for a in enabled)
            avail_notional = self.max_symbol_notional_total - existing_shares * price
            if avail_notional <= 0:
                return []
            cap_shares = int(avail_notional // price)
            total_qty = min(total_qty, cap_shares)
        if total_qty <= 0:
            return []

        weights = [a.allocation_weight for a in enabled]
        legs = _largest_remainder(total_qty, weights)

        # Per-account max_notional cap.
        if price is not None and price > 0:
            for i, acct in enumerate(enabled):
                if acct.max_notional > 0:
                    cap = int(acct.max_notional // price)
                    legs[i] = min(legs[i], cap)

        return self._build(strategy_id, symbol, Side.BUY, limit_price, signal_ts,
                           reason, extended_hours, enabled, legs)

    # ------------------------------------------------------------------ exit
    def _allocate_exit(self, strategy_id, symbol, total_qty, limit_price, signal_ts,
                       reason, extended_hours, enabled, held) -> list[OrderIntent]:
        held_list = [max(0, held.get(a.account_id, 0)) for a in enabled]
        total_held = sum(held_list)
        if total_held <= 0:
            return []
        target = min(total_qty, total_held)
        # Distribute the exit across accounts proportional to holdings, capped at held.
        legs = _largest_remainder(target, held_list)
        for i, h in enumerate(held_list):
            legs[i] = min(legs[i], h)
        return self._build(strategy_id, symbol, Side.SELL, limit_price, signal_ts,
                           reason, extended_hours, enabled, legs)

    # ------------------------------------------------------------------ build
    def _build(self, strategy_id, symbol, side, limit_price, signal_ts, reason,
               extended_hours, enabled, legs) -> list[OrderIntent]:
        order_type = OrderType.LIMIT if limit_price is not None else OrderType.MARKET
        intents: list[OrderIntent] = []
        for i, acct in enumerate(enabled):
            qty = legs[i]
            if qty <= 0:
                continue
            cid = client_order_id(
                strategy_id=strategy_id, account_id=acct.account_id, symbol=symbol,
                signal_ts=signal_ts, reason=reason, leg=i,
            )
            intents.append(OrderIntent(
                client_order_id=cid,
                account_id=acct.account_id,
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=order_type,
                limit_price=limit_price,
                tif=TimeInForce.DAY,
                extended_hours=extended_hours,
                strategy_id=strategy_id,
                reason=reason,
                created_at=signal_ts,
            ))
        return intents
