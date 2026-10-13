"""AlertOnlyBroker: never executes; operator-reported manual fills flow through."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sgt.brokers import AlertOnlyBroker
from sgt.brokers.alert_only import Notifier
from sgt.core.types import OrderStatus, Side

from brk_helpers import make_intent

UTC = timezone.utc


class RecordingNotifier:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, body: str) -> None:
        self.sent.append((title, body))


def _broker():
    n = RecordingNotifier()
    return AlertOnlyBroker("acct", n), n


async def test_submit_sends_instruction_and_returns_manual_pending():
    broker, n = _broker()
    intent = make_intent(cid="a1", qty=7)
    ack = await broker.submit(intent)
    assert ack.status is OrderStatus.PENDING
    assert ack.message == "manual"
    assert len(n.sent) == 1 and "AAA" in n.sent[0][1]


async def test_notifier_protocol():
    broker, n = _broker()
    assert isinstance(n, Notifier)


async def test_manual_fill_flows_through_fills_once():
    broker, _ = _broker()
    intent = make_intent(cid="a2", qty=10)
    await broker.submit(intent)
    broker.record_manual_fill(intent.client_order_id, "AAA", Side.BUY, 10, 5.25,
                              datetime(2026, 10, 12, 14, 40, tzinfo=UTC))
    first = [f async for f in broker.fills()]
    second = [f async for f in broker.fills()]
    assert len(first) == 1 and second == []
    assert first[0].price == 5.25 and first[0].qty == 10
    state = await broker.account_state()
    assert any(p.symbol == "AAA" and p.qty == 10 for p in state.positions)


async def test_manual_fill_requires_submitted_order():
    broker, _ = _broker()
    with pytest.raises(ValueError):
        broker.record_manual_fill("missing", "AAA", Side.BUY, 1, 5.0,
                                  datetime(2026, 10, 12, 14, 40, tzinfo=UTC))


async def test_manual_fill_cannot_exceed_ordered_qty():
    broker, _ = _broker()
    intent = make_intent(cid="a3", qty=5)
    await broker.submit(intent)
    with pytest.raises(ValueError):
        broker.record_manual_fill(intent.client_order_id, "AAA", Side.BUY, 6, 5.0,
                                  datetime(2026, 10, 12, 14, 40, tzinfo=UTC))


async def test_partial_manual_fills_mark_partially_filled():
    broker, _ = _broker()
    intent = make_intent(cid="a4", qty=10)
    await broker.submit(intent)
    broker.record_manual_fill(intent.client_order_id, "AAA", Side.BUY, 4, 5.0,
                              datetime(2026, 10, 12, 14, 40, tzinfo=UTC))
    orders = await broker.open_orders()
    assert any(o.status is OrderStatus.PARTIALLY_FILLED for o in orders)
