"""Submit serialization for adapters whose brokers do not dedupe client order ids.

Schwab and SnapTrade idempotency rests on the local ``order_ledger`` (check row -> POST ->
record). Two layers keep that check-then-act from double-submitting (R2-D, 2026-10-18):

* in-process: :class:`KeyedLocks` serializes concurrent ``submit`` calls for the SAME
  ``client_order_id`` (the long-only guard awaits the broker between the ledger check and
  the POST, so two coroutines could otherwise both see "no row" and both POST);
* cross-process: :func:`claim_ledger_row` inserts the row atomically -- the primary key
  makes the losing writer get ``False`` instead of proceeding to a second POST.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy.exc import IntegrityError

from sgt.accounts.models import OrderLedger
from sgt.core.types import OrderIntent


class KeyedLocks:
    """One ``asyncio.Lock`` per key, dropped once no coroutine holds or awaits it."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._waiters: dict[str, int] = {}

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._waiters[key] = self._waiters.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            self._waiters[key] -= 1
            if self._waiters[key] == 0:
                del self._waiters[key]
                self._locks.pop(key, None)

    def __len__(self) -> int:
        return len(self._locks)


def claim_ledger_row(intent: OrderIntent, status: str, intent_json: dict,
                     now: datetime) -> bool:
    """Atomically insert the ledger row for ``intent``. False if any writer already has it."""
    from sgt.db.session import get_session

    try:
        with get_session() as session:
            if session.get(OrderLedger, intent.client_order_id) is not None:
                return False
            session.add(OrderLedger(
                client_order_id=intent.client_order_id, account_id=intent.account_id,
                broker_order_id=None, status=status, intent=intent_json, message="",
                submitted_at=intent.created_at, updated_at=now))
    except IntegrityError:
        return False   # lost the insert race to another process
    return True
