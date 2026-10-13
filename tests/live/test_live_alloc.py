"""Per-account caps in multi-account allocation (G-4): the weight split is bounded by
each account's registry ``max_notional`` and a disabled account is excluded."""
from __future__ import annotations

from datetime import datetime, timezone

from live_fakes import CFG

from sgt.accounts.registry import AccountSpec
from sgt.brokers.alert_only import LogNotifier
from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.brokers.sim import SimBroker
from sgt.core.types import Mode
from sgt.engine.backtest import default_risk_config
from sgt.engine.engine import StrategyEngine
from sgt.engine.eventlog import InMemoryEventSink
from sgt.live.alerts import AlertManager
from sgt.live.executor import LiveExecutor
from sgt.risk.exposure import DictExposureView
from sgt.risk.limits import LadderStage
from sgt.risk.manager import RiskManager
from sgt.risk.state import InMemoryRiskStore
from sgt.strategy.base import Decision
from sgt.strategy.loader import config_hash as compute_config_hash

_TS = datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc)


def _executor(specs):
    engine = StrategyEngine(CFG, allow_unknown_float=True)
    adapters = {s.account_id: SimBroker(s.account_id, DEFAULT_COST_MODEL, 100_000.0) for s in specs}
    exposure = DictExposureView()
    store = InMemoryRiskStore(namespace="alloc-test")
    risk = RiskManager(default_risk_config(CFG), exposure, store=store, mode=Mode.SHADOW)
    risk.arm(actor="test", reason="test")
    store.upsert_strategy(CFG.id, enabled=True, stage=LadderStage.STANDARD)
    return LiveExecutor(
        CFG, engine, risk=risk, adapters=adapters, exposure=exposure,
        cost_model=DEFAULT_COST_MODEL, event_sink=InMemoryEventSink(),
        candidate_sink=lambda c: None, alerts=AlertManager(LogNotifier()),
        run_id="alloc-test", config_hash=compute_config_hash(CFG), mode=Mode.SHADOW,
        accounts=specs)


def _entry(qty, price=10.0):
    return Decision(kind="entry", symbol="AAAA", reason="entry", signal_ts=_TS,
                    limit_price=price, stop_price=price * 0.95, qty_hint=qty)


def test_max_notional_caps_one_account_leg():
    specs = [AccountSpec("A", "sim", "cash", True, 1.0, 0.0),      # unlimited
             AccountSpec("B", "sim", "cash", True, 1.0, 50.0)]      # cap = 50 // 10 = 5 shares
    ex = _executor(specs)
    legs = dict(ex._allocate(_entry(20, price=10.0)))
    assert legs == {"A": 10, "B": 5}, legs


def test_disabled_account_excluded_from_allocation():
    specs = [AccountSpec("A", "sim", "cash", True, 1.0, 0.0),
             AccountSpec("B", "sim", "cash", False, 1.0, 0.0)]      # disabled
    ex = _executor(specs)
    legs = dict(ex._allocate(_entry(20)))
    assert legs == {"A": 20}, legs


def test_exit_allocates_to_all_enabled_accounts():
    specs = [AccountSpec("A", "sim", "cash", True, 1.0, 0.0),
             AccountSpec("B", "sim", "cash", True, 1.0, 50.0)]
    ex = _executor(specs)
    legs = dict(ex._allocate(Decision(kind="exit", symbol="AAAA", reason="exit:x",
                                      signal_ts=_TS, limit_price=10.0, qty_hint=7)))
    assert set(legs) == {"A", "B"}, legs
