"""Per-account reconciliation: compare our ledger to the broker and fail closed.

Runs at startup and on a schedule (docs/IMPLEMENTATION_PLAN.md §13.3). Any position
mismatch, a symbol the broker reports that our ledger does not know, or cash drift
beyond tolerance halts that account via a caller-provided callback — a guardrail must
fail closed rather than keep trading on a divergent view of the world.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from sgt.core.interfaces import BrokerAdapter
from sgt.core.types import AccountState

_DEFAULT_CASH_TOLERANCE = 1.0  # USD


@dataclass(frozen=True, slots=True)
class Mismatch:
    symbol: str
    ledger_qty: int
    broker_qty: int
    kind: str  # "qty_diff" | "unknown_symbol"


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    account_id: str
    ok: bool
    mismatches: tuple[Mismatch, ...] = ()
    cash_drift: float = 0.0
    cash_ok: bool = True

    @property
    def reason(self) -> str:
        parts = []
        if self.mismatches:
            parts.append("; ".join(
                f"{m.symbol} ledger={m.ledger_qty} broker={m.broker_qty} ({m.kind})"
                for m in self.mismatches))
        if not self.cash_ok:
            parts.append(f"cash drift {self.cash_drift:+.2f}")
        return " | ".join(parts) or "ok"


def reconcile(
    account_id: str,
    ledger_positions: Mapping[str, int],
    broker_state: AccountState,
    *,
    ledger_cash: float | None = None,
    cash_tolerance: float = _DEFAULT_CASH_TOLERANCE,
) -> ReconcileResult:
    """Compare ledger positions/cash against a broker ``AccountState``."""
    broker_pos = {p.symbol: p.qty for p in broker_state.positions}
    symbols = set(ledger_positions) | set(broker_pos)
    mismatches: list[Mismatch] = []
    for sym in sorted(symbols):
        lq = int(ledger_positions.get(sym, 0))
        bq = int(broker_pos.get(sym, 0))
        if lq == bq:
            continue
        kind = "unknown_symbol" if sym not in ledger_positions else "qty_diff"
        mismatches.append(Mismatch(sym, lq, bq, kind))

    cash_drift = 0.0
    cash_ok = True
    if ledger_cash is not None:
        cash_drift = broker_state.cash - ledger_cash
        cash_ok = abs(cash_drift) <= cash_tolerance

    ok = not mismatches and cash_ok
    return ReconcileResult(account_id, ok, tuple(mismatches), cash_drift, cash_ok)


class Reconciler:
    """Runs reconciliation against a live adapter and halts the account on mismatch."""

    def __init__(
        self,
        ledger_provider: Callable[[str], Mapping[str, int]],
        *,
        on_halt: Callable[[str, str], None],
        ledger_cash_provider: Callable[[str], float] | None = None,
        cash_tolerance: float = _DEFAULT_CASH_TOLERANCE,
    ) -> None:
        self._ledger_provider = ledger_provider
        self._on_halt = on_halt
        self._ledger_cash_provider = ledger_cash_provider
        self._cash_tolerance = cash_tolerance

    async def run(self, adapter: BrokerAdapter) -> ReconcileResult:
        state = await adapter.account_state()
        account_id = adapter.account_id
        ledger_pos = self._ledger_provider(account_id)
        ledger_cash = (self._ledger_cash_provider(account_id)
                       if self._ledger_cash_provider is not None else None)
        result = reconcile(account_id, ledger_pos, state,
                           ledger_cash=ledger_cash, cash_tolerance=self._cash_tolerance)
        if not result.ok:
            self._on_halt(account_id, result.reason)
        return result
