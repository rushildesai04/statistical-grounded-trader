"""Current-holdings view injected into the risk manager.

The authoritative view of what is held *right now* across all accounts lives in the
account router / reconciliation layer (sgt.accounts, §13.3), not in the risk manager.
The risk manager consumes this read-only protocol to:
- detect exits (a SELL that reduces an existing long) and reject shorts / over-sells,
- enforce the aggregate per-symbol notional cap across all accounts,
- enforce the max-open-positions portfolio cap.

A simple dict-backed implementation (``DictExposureView``) is provided for tests,
standalone use and the SimBroker path; production injects the router's own view.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ExposureView(Protocol):
    def symbol_notional(self, symbol: str) -> float:
        """Aggregate market value held in ``symbol`` across ALL accounts (USD)."""
        ...

    def position_qty(self, account_id: str, symbol: str) -> int:
        """Long share count held in ``symbol`` within ``account_id`` (>= 0)."""
        ...

    def open_position_count(self) -> int:
        """Number of distinct non-flat positions across all accounts."""
        ...


class DictExposureView:
    """In-memory ExposureView. ``positions`` maps (account_id, symbol) -> qty;
    ``marks`` maps symbol -> last price used to value aggregate notional."""

    def __init__(self, positions: dict[tuple[str, str], int] | None = None,
                 marks: dict[str, float] | None = None) -> None:
        self._positions: dict[tuple[str, str], int] = dict(positions or {})
        self._marks: dict[str, float] = dict(marks or {})

    def set_position(self, account_id: str, symbol: str, qty: int, mark: float | None = None) -> None:
        if qty <= 0:
            self._positions.pop((account_id, symbol), None)
        else:
            self._positions[(account_id, symbol)] = qty
        if mark is not None:
            self._marks[symbol] = mark

    def set_mark(self, symbol: str, mark: float) -> None:
        self._marks[symbol] = mark

    def symbol_notional(self, symbol: str) -> float:
        mark = self._marks.get(symbol, 0.0)
        total = sum(q for (acc, sym), q in self._positions.items() if sym == symbol)
        return total * mark

    def position_qty(self, account_id: str, symbol: str) -> int:
        return self._positions.get((account_id, symbol), 0)

    def open_position_count(self) -> int:
        return sum(1 for q in self._positions.values() if q > 0)
