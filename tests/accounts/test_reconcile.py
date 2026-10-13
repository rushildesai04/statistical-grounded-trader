"""Reconciliation + ledger: mismatch detection, halt-on-mismatch, idempotent writes."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sgt.accounts import ledger as ledger_mod
from sgt.accounts.reconcile import Reconciler, reconcile
from sgt.core.types import (
    AccountState,
    Fill,
    OrderAck,
    OrderStatus,
    Position,
    Side,
)
from sgt.db.session import get_session

from acct_helpers import seeded_sim

UTC = timezone.utc


def _state(account_id="A", cash=1000.0, positions=()):
    return AccountState(account_id, cash, cash, cash, tuple(positions))


def test_reconcile_clean():
    state = _state(positions=[Position("AAA", 10, 5.0)])
    res = reconcile("A", {"AAA": 10}, state, ledger_cash=1000.0)
    assert res.ok and not res.mismatches


def test_reconcile_qty_mismatch():
    state = _state(positions=[Position("AAA", 10, 5.0)])
    res = reconcile("A", {"AAA": 5}, state)
    assert not res.ok
    m = res.mismatches[0]
    assert m.symbol == "AAA" and m.ledger_qty == 5 and m.broker_qty == 10
    assert m.kind == "qty_diff"


def test_reconcile_unknown_symbol():
    state = _state(positions=[Position("ZZZ", 3, 2.0)])
    res = reconcile("A", {}, state)
    assert res.mismatches[0].kind == "unknown_symbol"


def test_reconcile_cash_drift():
    state = _state(cash=1000.0)
    res = reconcile("A", {}, state, ledger_cash=900.0, cash_tolerance=1.0)
    assert not res.ok and not res.cash_ok
    assert res.cash_drift == pytest.approx(100.0)


async def test_reconciler_halts_on_mismatch():
    adapter = seeded_sim("A", "AAA", 10, 5.0)
    halted: list[tuple[str, str]] = []
    rec = Reconciler(lambda aid: {"AAA": 3}, on_halt=lambda a, r: halted.append((a, r)))
    res = await rec.run(adapter)
    assert not res.ok
    assert halted and halted[0][0] == "A"


async def test_reconciler_clean_does_not_halt():
    adapter = seeded_sim("A", "AAA", 10, 5.0)
    halted: list = []
    rec = Reconciler(lambda aid: {"AAA": 10}, on_halt=lambda a, r: halted.append(a))
    res = await rec.run(adapter)
    assert res.ok and halted == []


def test_record_ack_idempotent_and_no_terminal_regression():
    intent = _mk_intent()
    with get_session() as s:
        ack1 = OrderAck(intent.client_order_id, "brk-1", OrderStatus.FILLED,
                        datetime.now(UTC), "filled")
        ledger_mod.record_ack(s, ack1, intent)
        # A stale WORKING arriving later must not regress a FILLED order.
        stale = OrderAck(intent.client_order_id, "brk-1", OrderStatus.WORKING,
                         datetime.now(UTC), "working")
        ledger_mod.record_ack(s, stale, intent)
    with get_session() as s:
        from sgt.accounts.models import OrderLedger
        row = s.get(OrderLedger, intent.client_order_id)
        assert row.status == OrderStatus.FILLED.value


def test_record_fill_dedupe_and_positions():
    with get_session() as s:
        f = Fill("cid", "brk", "A", "AAA", Side.BUY, 10, 5.0,
                 datetime.now(UTC), "fill-1")
        assert ledger_mod.record_fill(s, f) is True
        assert ledger_mod.record_fill(s, f) is False  # dedupe on fill_id
        f2 = Fill("cid", "brk", "A", "AAA", Side.SELL, 4, 6.0,
                  datetime.now(UTC), "fill-2")
        ledger_mod.record_fill(s, f2)
    with get_session() as s:
        assert ledger_mod.ledger_positions(s, "A") == {"AAA": 6}


def _mk_intent():
    from sgt.core.types import OrderIntent, OrderType, TimeInForce
    return OrderIntent("cid-x", "A", "AAA", Side.BUY, 10, OrderType.LIMIT, 5.0,
                       TimeInForce.DAY, False, "strat", "entry", datetime.now(UTC))
