"""Shared BrokerAdapter contract suite, parametrized over every adapter.

Enforces the invariants in sgt.core.interfaces.BrokerAdapter: idempotent submit,
cancel, and fills() yielding each execution exactly once.
"""
from __future__ import annotations

import pytest

from sgt.core.types import OrderStatus, Side


async def _drain(adapter):
    return [f async for f in adapter.fills()]


async def test_connect_and_account_state(harness):
    state = await harness.adapter.account_state()
    assert state.account_id == "acct"


async def test_submit_is_idempotent(harness):
    intent = harness.intent("idem-cid")
    ack1 = await harness.adapter.submit(intent)
    ack2 = await harness.adapter.submit(intent)
    assert ack1.client_order_id == ack2.client_order_id == intent.client_order_id
    assert ack1.broker_order_id == ack2.broker_order_id
    assert ack1.status == ack2.status


async def test_cancel_returns_canceled(harness):
    intent = harness.intent("cancel-cid")
    await harness.adapter.submit(intent)
    ack = await harness.adapter.cancel(intent.client_order_id)
    assert ack.status is OrderStatus.CANCELED
    assert ack.client_order_id == intent.client_order_id


async def test_fills_exactly_once(harness):
    intent = await harness.arrange_one_fill()
    first = await _drain(harness.adapter)
    second = await _drain(harness.adapter)
    assert len(first) == 1, first
    assert second == []
    fill = first[0]
    assert fill.client_order_id == intent.client_order_id
    assert fill.qty == 10
    assert fill.account_id == "acct"


async def test_health_ok(harness):
    health = await harness.adapter.health()
    assert health.ok is True


async def test_open_orders_shape(harness):
    assert isinstance(await harness.adapter.open_orders(), list)


async def test_open_orders_carry_symbol_side_remaining(harness):
    # Exposure views (router aggregate cap) rely on these fields being populated.
    await harness.arrange_open_order()
    acks = await harness.adapter.open_orders()
    assert acks, "expected at least one open order"
    for a in acks:
        assert a.symbol == "AAA"
        assert a.side is Side.BUY
        assert a.remaining_qty is not None and a.remaining_qty > 0
