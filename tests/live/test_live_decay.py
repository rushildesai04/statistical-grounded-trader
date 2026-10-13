"""Decay wiring: closed-trade monitor signals map to the correct risk action and are
audited (plan §11.4)."""
from __future__ import annotations

from datetime import datetime, timezone

from live_fakes import CFG, RecordingNotifier

from sgt.engine.backtest import default_risk_config
from sgt.engine.eventlog import InMemoryEventSink
from sgt.learning.decay import (
    Action,
    DecaySupervisor,
    DrawdownBudgetMonitor,
    MonitorSignal,
    RollingExpectancyMonitor,
    SlippageDriftMonitor,
)
from sgt.live.alerts import AlertManager
from sgt.live.decay_wiring import DecayWiring
from sgt.risk.exposure import DictExposureView
from sgt.risk.limits import LadderStage
from sgt.risk.manager import RiskManager
from sgt.risk.state import InMemoryRiskStore
from sgt.core.types import Mode

_TS = datetime(2024, 3, 12, 14, 30, tzinfo=timezone.utc)
_SID = CFG.id


def _wiring(monitors):
    store = InMemoryRiskStore(namespace="decay-test")
    risk = RiskManager(default_risk_config(CFG), DictExposureView(), store=store, mode=Mode.LIVE)
    risk.arm(actor="test", reason="test")
    store.upsert_strategy(_SID, enabled=True, stage=LadderStage.STANDARD)
    rec = RecordingNotifier()
    sink = InMemoryEventSink()
    wiring = DecayWiring(risk, event_sink=sink, alerts=AlertManager(rec))
    return wiring, DecaySupervisor(monitors), risk, store, sink, rec


def _audit_kinds(store):
    return {kind for _ts, kind, _d in store.iter_audit()}


async def test_halve_sets_size_multiplier():
    wiring, sup, risk, store, sink, rec = _wiring(
        {"re": RollingExpectancyMonitor(0.01, window=3)})
    sig = None
    for _ in range(3):
        sig = wiring.on_closed_trade(_SID, sup, ts=_TS, trade_return=-0.02, trade_pnl=-20.0)
    assert sig is not None and sig.action is Action.HALVE
    assert store.get_strategy(_SID).size_multiplier == 0.5
    assert "size_multiplier_set" in _audit_kinds(store)
    assert any(e["kind"] == "decay_action" for e in sink.events)


async def test_disable_turns_strategy_off():
    wiring, sup, risk, store, sink, rec = _wiring({"dd": DrawdownBudgetMonitor(100.0)})
    sig = wiring.on_closed_trade(_SID, sup, ts=_TS, trade_return=-1.0, trade_pnl=-100.0)
    assert sig is not None and sig.action is Action.DISABLE
    assert store.get_strategy(_SID).enabled is False
    assert "strategy_disabled" in _audit_kinds(store)


async def test_halt_disarms_namespace():
    wiring, sup, risk, store, sink, rec = _wiring(
        {"slip": SlippageDriftMonitor(window=2, threshold=1.5)})
    wiring.on_closed_trade(_SID, sup, ts=_TS, trade_return=0.0, trade_pnl=0.0,
                           realized_slippage=3.0, modeled_slippage=1.0)
    sig = wiring.on_closed_trade(_SID, sup, ts=_TS, trade_return=0.0, trade_pnl=0.0,
                                 realized_slippage=3.0, modeled_slippage=1.0)
    assert sig is not None and sig.action is Action.HALT
    assert store.get_switch().armed is False
    assert "switch_disarmed" in _audit_kinds(store)
    assert any("HALT" in title for title, _ in rec.sent)


class _FlagMonitor:
    name = "cusum"  # a trade-return monitor name so the wiring feeds it

    def update(self, _trade_return):
        return MonitorSignal(action=Action.FLAG, monitor="cusum", detail="psi-like flag")


async def test_flag_alerts_and_journals_without_risk_change():
    wiring, sup, risk, store, sink, rec = _wiring({"flag": _FlagMonitor()})
    sig = wiring.on_closed_trade(_SID, sup, ts=_TS, trade_return=0.01, trade_pnl=5.0)
    assert sig is not None and sig.action is Action.FLAG
    assert store.get_strategy(_SID).enabled is True
    assert store.get_strategy(_SID).size_multiplier == 1.0
    assert store.get_switch().armed is True
    assert any("FLAG" in title for title, _ in rec.sent)
    assert any(e["kind"] == "decay_action" for e in sink.events)
