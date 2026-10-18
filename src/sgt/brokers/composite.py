"""Alert-only execution backed by a broker's real positions (read-only connection).

For brokerages with no compliant order API (Robinhood), orders go to the operator as
alerts while positions and cash come from the broker itself through a read-only
connection (SnapTrade). Reconciliation, exposure and risk therefore see the real
account instead of only manually recorded fills.
"""
from __future__ import annotations

from typing import Protocol

from sgt.brokers.alert_only import AlertOnlyBroker, Notifier
from sgt.core.types import AccountState


class PositionSource(Protocol):
    account_id: str

    async def account_state(self) -> AccountState: ...

    async def close(self) -> None: ...


class AlertWithBrokerPositions(AlertOnlyBroker):
    """AlertOnlyBroker whose ``account_state`` is the broker's read-only snapshot."""

    name = "alert_with_positions"

    def __init__(self, account_id: str, notifier: Notifier, positions: PositionSource) -> None:
        if positions.account_id != account_id:
            raise ValueError(
                f"position source account {positions.account_id!r} != adapter account {account_id!r}")
        super().__init__(account_id, notifier)
        self._positions = positions

    async def account_state(self) -> AccountState:
        return await self._positions.account_state()

    async def close(self) -> None:
        await self._positions.close()
        await super().close()
