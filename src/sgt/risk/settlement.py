"""Settled-cash tracking for cash / IRA accounts (IMPLEMENTATION_PLAN §2, §13.3).

Cash and IRA accounts cannot use unsettled proceeds to buy, and selling a position
that was bought with unsettled funds before those funds settle is a good-faith
violation (GFV). US equities settle T+1 *in trading sessions*, so a Friday sale
settles at the next session (Monday), not Saturday. We therefore advance settlement
by SESSION, never by calendar day: ``start_session`` ticks a monotonic session
counter and releases any proceeds whose settle-session has been reached.

Scope: this tracker enforces "buys use settled cash" and detects would-be good-faith
violations. Margin accounts are not registered and are exempt (``is_cash_account``
returns False), so the manager skips the settled-cash check for them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sgt.core.types import Fill, Side


@dataclass
class _Account:
    settled_cash: float
    # proceeds pending settlement: list of (settle_at_session, amount)
    pending: list[tuple[int, float]] = field(default_factory=list)
    # per-symbol shares that were purchased with unsettled funds (GFV exposure),
    # as a list of (settle_at_session, qty) lots that free the GFV flag once settled.
    unsettled_lots: dict[str, list[tuple[int, int]]] = field(default_factory=dict)


class SettlementTracker:
    """T+1 settled-cash and good-faith-violation tracker for cash/IRA accounts."""

    #: trading sessions until sale proceeds settle (T+1).
    SETTLE_SESSIONS = 1

    def __init__(self) -> None:
        self._accounts: dict[str, _Account] = {}
        self._session = 0  # monotonic trading-session counter

    # ----------------------------------------------------------------- setup
    def register(self, account_id: str, starting_settled_cash: float) -> None:
        if starting_settled_cash < 0:
            raise ValueError("starting settled cash must be >= 0")
        self._accounts[account_id] = _Account(settled_cash=starting_settled_cash)

    def is_cash_account(self, account_id: str) -> bool:
        return account_id in self._accounts

    # ----------------------------------------------------------------- sessions
    def start_session(self) -> None:
        """Advance to the next trading session and release matured settlements."""
        self._session += 1
        for acct in self._accounts.values():
            matured = [(when, amt) for when, amt in acct.pending if when <= self._session]
            if matured:
                acct.settled_cash += sum(amt for _, amt in matured)
                acct.pending = [(w, a) for w, a in acct.pending if w > self._session]
            for sym in list(acct.unsettled_lots):
                acct.unsettled_lots[sym] = [(w, q) for w, q in acct.unsettled_lots[sym] if w > self._session]
                if not acct.unsettled_lots[sym]:
                    del acct.unsettled_lots[sym]

    # ----------------------------------------------------------------- reads
    def available_settled_cash(self, account_id: str) -> float:
        acct = self._accounts.get(account_id)
        return acct.settled_cash if acct else 0.0

    def pending_settlement(self, account_id: str) -> float:
        acct = self._accounts.get(account_id)
        return sum(amt for _, amt in acct.pending) if acct else 0.0

    def would_violate_good_faith(self, account_id: str, symbol: str, qty: int) -> bool:
        """True if selling ``qty`` of ``symbol`` now would be a good-faith violation
        (the position was, in whole or part, bought with funds not yet settled)."""
        acct = self._accounts.get(account_id)
        if acct is None:
            return False
        lots = acct.unsettled_lots.get(symbol)
        return bool(lots) and qty > 0

    # ----------------------------------------------------------------- updates
    def record_fill(self, fill: Fill) -> None:
        """Apply a fill to settled cash. Buys consume settled cash first; any shortfall
        is funded by unsettled cash and flagged as GFV exposure. Sells add proceeds to
        the pending-settlement bucket maturing at the next session."""
        acct = self._accounts.get(fill.account_id)
        if acct is None:
            return  # not a cash account: no settled-cash constraint
        notional = fill.price * fill.qty + fill.fee
        if fill.side is Side.BUY:
            from_settled = min(acct.settled_cash, notional)
            acct.settled_cash -= from_settled
            shortfall = notional - from_settled
            if shortfall > 0:
                # Funded with unsettled cash -> the whole lot carries GFV exposure
                # until the oldest pending proceeds settle.
                settle_at = min((w for w, _ in acct.pending), default=self._session + self.SETTLE_SESSIONS)
                acct.unsettled_lots.setdefault(fill.symbol, []).append((settle_at, fill.qty))
        else:  # SELL: proceeds settle T+1 sessions from now
            acct.pending.append((self._session + self.SETTLE_SESSIONS, notional))
            # Selling reduces any unsettled lot of this symbol (position being flattened).
            acct.unsettled_lots.pop(fill.symbol, None)
