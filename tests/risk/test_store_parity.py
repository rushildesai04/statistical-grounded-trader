"""Behavioral parity: the RiskManager must behave identically on the DB-backed
RiskStore and the in-memory InMemoryRiskStore (used for backtests/search)."""
from __future__ import annotations

import pytest

from sgt.core.types import RiskVerdict
from sgt.risk import InMemoryRiskStore, RiskStore

from .conftest import buy, ctx, fill_from, make_config, make_manager, sell


@pytest.fixture(params=["db", "mem"])
def store(request):
    return RiskStore("live") if request.param == "db" else InMemoryRiskStore("live")


def test_disarmed_rejects_entry(store):
    m, _ = make_manager(store=store, armed=False)
    assert "disarmed" in set(m.check(buy(), ctx()).reasons)


def test_reduce_to_fit_max_loss(store):
    m, _ = make_manager(store=store)
    dec = m.check(buy(qty=600, limit=5.0, stop=4.9), ctx())
    assert dec.verdict is RiskVerdict.REDUCE and dec.qty == 500


def test_daily_loss_blocks_entry_allows_exit(store):
    from sgt.risk import DictExposureView
    exp = DictExposureView()
    m, _ = make_manager(exposure=exp, store=store)
    m.on_fill(fill_from(buy(qty=200, limit=5.0), price=5.0, fill_id="b"))
    exp.set_position("A", "WFF", 200, mark=5.0)
    m.on_fill(fill_from(sell(qty=100, limit=3.0), price=3.0, fill_id="s"))  # -200 per-account
    exp.set_position("A", "WFF", 100, mark=5.0)
    assert "daily_loss_limit_account" in set(m.check(buy(account="A"), ctx()).reasons)
    assert m.check(sell(qty=100), ctx()).verdict is RiskVerdict.APPROVE


def test_idempotent_on_fill(store):
    from sgt.risk import DictExposureView
    exp = DictExposureView()
    m, _ = make_manager(exposure=exp, store=store)
    m.on_fill(fill_from(buy(qty=100, limit=5.0), price=5.0, fill_id="b1"))
    f = fill_from(sell(qty=100, limit=3.0), price=3.0, fill_id="s1")
    m.on_fill(f)
    first = m.program_realized_pnl
    m.on_fill(f)
    assert m.program_realized_pnl == first
    assert len([r for r in store.iter_ledger() if r.fill_id == "s1"]) == 1


def test_budget_auto_disarm(store):
    from sgt.risk import DictExposureView
    exp = DictExposureView()
    m, _ = make_manager(exposure=exp, store=store)
    m.on_fill(fill_from(buy(qty=200, limit=5.0), price=5.0, fill_id="b"))
    exp.set_position("A", "WFF", 200, mark=5.0)
    m.on_fill(fill_from(sell(qty=200, limit=2.5), price=2.5, fill_id="s"))  # -500 == budget
    assert store.get_switch().armed is False
    assert "learning_budget_exhausted" in {k for _, k, _ in store.iter_audit()}
