"""Restart safety for the live runtime (plan §11.4, §3.1).

L-B: a restart rehydrates the decay supervisor by replaying the strategy's persisted
CLOSED trades, so CUSUM/rolling/drawdown/win-rate state survives and the next loss trips
exactly as it would have without the restart.
L-D: the decay size-multiplier ratchet is seeded from the persisted value, so a restart
compounds from it instead of resetting to 1.0.
L-E: a stale feed with open positions hands off to the executor exactly once per stall.
L-C: the runner refuses an adapter whose liveness disagrees with the run mode.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from live_fakes import CFG, dataset

from sgt.accounts.registry import AccountSpec
from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.brokers.sim import SimBroker
from sgt.core.types import Mode
from sgt.db.session import get_session
from sgt.engine.backtest import default_risk_config
from sgt.learning.decay import DecaySupervisor, RollingExpectancyMonitor
from sgt.live.alerts import AlertManager
from sgt.live.decay_wiring import DecayWiring, build_supervisor
from sgt.live.replay import InMemoryLiveSource
from sgt.live.runner import AdapterModeMismatchError, LiveRunner
from sgt.risk.exposure import DictExposureView
from sgt.risk.limits import LadderStage
from sgt.risk.manager import RiskManager
from sgt.risk.state import InMemoryRiskStore, RiskStore
from sgt.search import registry
from sgt.search.registry import DecayBaseline
from sgt.strategy.loader import config_hash as compute_config_hash

_SPEC = AccountSpec("acct", "sim", "cash", True, 1.0, 0.0)
_HASH = compute_config_hash(CFG)
_TS = datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc)
_SID = CFG.id


async def _noop_sleep(_s: float) -> None:
    return None


class _LiveSim(SimBroker):
    is_paper = False  # live-capable stand-in so LIVE runners pass the adapter guardrail


def _runner(mode, *, adapter=None):
    if adapter is None:
        adapter = (_LiveSim if mode is Mode.LIVE else SimBroker)("acct", DEFAULT_COST_MODEL, 100_000.0)
    rt = InMemoryLiveSource([], delay_minutes=0, consolidated=True)
    return LiveRunner("tests/fixtures/trader_v0_mechanics.yaml", mode, [_SPEC], source=rt,
                      adapters={"acct": adapter}, risk_config=default_risk_config(CFG),
                      clock=lambda: _TS, sleep=_noop_sleep, run_id=f"{mode.value}-{id(adapter)}")


def _write_baseline():
    with get_session() as s:
        registry.upsert_registry(s, config_hash=_HASH, config={}, status="validated",
                                 evidence={"decay_baseline": {
                                     "expectancy": 0.01, "ret_std": 0.02, "win_rate": 0.55}})


def _loss_row(i: int, *, ret: float = -0.024, pnl: float = -50.0) -> dict:
    ts = _TS + timedelta(minutes=i)
    return {"account_id": "acct", "strategy_id": _SID, "symbol": "AAAA",
            "entry_ts": ts, "entry_price": 6.0, "exit_ts": ts, "exit_price": 5.85,
            "qty": 100, "pnl": pnl, "ret": ret, "exit_reason": "stop", "mfe": 0.0,
            "mae": -0.03, "bars_held": 3, "halted_during": False,
            "modeled_slippage": 0.0, "realized_slippage": 0.0}


def _slip_row(i: int, *, realized: float = 5.0, modeled: float = 1.0) -> dict:
    # High realized-vs-modeled slippage, but return == baseline expectancy with a winning
    # PnL so ONLY slippage_drift can fire (CUSUM/rolling/win-rate/drawdown stay quiet).
    ts = _TS + timedelta(minutes=i)
    return {"account_id": "acct", "strategy_id": _SID, "symbol": "AAAA",
            "entry_ts": ts, "entry_price": 6.0, "exit_ts": ts, "exit_price": 6.06,
            "qty": 100, "pnl": 6.0, "ret": 0.01, "exit_reason": "take_profit", "mfe": 0.02,
            "mae": 0.0, "bars_held": 3, "halted_during": False,
            "modeled_slippage": modeled, "realized_slippage": realized}


def _actions(runner, action: str) -> list[str]:
    return [e["payload"]["action"] for e in runner.sink.events
            if e["kind"] == "decay_action" and e["payload"]["action"] == action]


def _demotes(runner) -> list[str]:
    return _actions(runner, "demote")


# ----------------------------------------------------------------------- L-B rehydration
async def test_restart_rehydrates_decay_state_and_next_loss_trips():
    _write_baseline()
    # Control: a fresh supervisor fed a SINGLE such loss does NOT trip (proves the streak,
    # not the one trade, is what fires the CUSUM DEMOTE).
    control = build_supervisor(DecayBaseline(expectancy=0.01, ret_std=0.02, win_rate=0.55),
                               dd_budget=1.0e12)
    assert control.monitors["cusum"].update(-0.024) is None

    # Run 1: four losses, journaled; CUSUM accumulates below its threshold -> no demote yet.
    r1 = _runner(Mode.LIVE)
    await r1.startup()
    for i in range(4):
        r1._on_closed_trade(_loss_row(i))
    assert not _demotes(r1), "four sub-threshold losses must not demote yet"
    s_run1 = r1._supervisor.monitors["cusum"].s
    assert s_run1 > 0

    # Restart: a brand-new runner over the same DB replays the four persisted closed trades.
    r2 = _runner(Mode.LIVE)
    await r2.startup()
    assert r2._supervisor.monitors["cusum"].s == pytest.approx(s_run1)  # CUSUM restored
    assert r2._supervisor.monitors["drawdown_budget"].equity < 0        # drawdown consumed
    assert any(e["kind"] == "decay_rehydrated" for e in r2.sink.events)

    # The next loss trips the DEMOTE exactly as it would have without the restart.
    r2._on_closed_trade(_loss_row(4))
    assert _demotes(r2), "restored CUSUM state must trip on the next loss"


# -------------------------------------------------------------- L-B slippage rehydration
async def test_restart_rehydrates_slippage_and_halt_fires_on_window():
    _write_baseline()
    # Control: a fresh slippage monitor fed only 5 high-slippage trades does NOT trip — the
    # 20-trade window is not full. The streak carried across the restart is what HALTs.
    control = build_supervisor(DecayBaseline(expectancy=0.01, ret_std=0.02, win_rate=0.55),
                               dd_budget=1.0e12)
    for _ in range(5):
        assert control.monitors["slippage_drift"].update(5.0, 1.0) is None

    # Run 1: 15 high-slippage trades journaled; window (20) not full -> no HALT yet.
    r1 = _runner(Mode.LIVE)
    await r1.startup()
    for i in range(15):
        r1._on_closed_trade(_slip_row(i))
    assert not _actions(r1, "halt"), "15 high-slippage trades (< window) must not HALT"

    # Restart: a new runner replays the 15 persisted closed trades into the slippage monitor.
    r2 = _runner(Mode.LIVE)
    await r2.startup()
    assert len(r2._supervisor.monitors["slippage_drift"]._realized) == 15  # slippage restored
    assert not _actions(r2, "halt"), "rehydration replays STATE only; it must not re-fire"
    r2.risk.arm(actor="test", reason="test")
    assert RiskStore("live").get_switch().armed

    # 5 more trades fill the 20-trade window -> HALT fires, exactly as without a restart.
    for i in range(15, 20):
        r2._on_closed_trade(_slip_row(i))
    assert _actions(r2, "halt"), "restored slippage state must HALT on reaching the window"
    assert not RiskStore("live").get_switch().armed, "HALT must disarm the namespace"


# ----------------------------------------------------------------------- L-D multiplier
async def test_halve_ratchet_compounds_from_persisted_multiplier():
    store = InMemoryRiskStore(namespace="mult-unit")
    risk = RiskManager(default_risk_config(CFG), DictExposureView(), store=store, mode=Mode.LIVE)
    risk.arm(actor="t", reason="t")
    store.upsert_strategy(_SID, enabled=True, stage=LadderStage.STANDARD)
    risk.set_size_multiplier(_SID, 0.5)  # a prior run already halved once

    wiring = DecayWiring(risk, event_sink=_sink(), alerts=AlertManager(_null()))
    wiring.seed_multiplier(_SID, risk.strategy_size_multiplier(_SID))  # restart seeding (L-D)
    sup = DecaySupervisor({"re": RollingExpectancyMonitor(0.01, window=3)})
    for _ in range(3):
        wiring.on_closed_trade(_SID, sup, ts=_TS, trade_return=-0.02, trade_pnl=-20.0)
    assert store.get_strategy(_SID).size_multiplier == 0.25  # compounded 0.5 -> 0.25

    # Without the seed, the same HALVE would reset to 0.5 (the defect L-D fixes).
    store2 = InMemoryRiskStore(namespace="mult-unit-2")
    risk2 = RiskManager(default_risk_config(CFG), DictExposureView(), store=store2, mode=Mode.LIVE)
    risk2.arm(actor="t", reason="t")
    store2.upsert_strategy(_SID, enabled=True, stage=LadderStage.STANDARD)
    risk2.set_size_multiplier(_SID, 0.5)
    wiring2 = DecayWiring(risk2, event_sink=_sink(), alerts=AlertManager(_null()))
    sup2 = DecaySupervisor({"re": RollingExpectancyMonitor(0.01, window=3)})
    for _ in range(3):
        wiring2.on_closed_trade(_SID, sup2, ts=_TS, trade_return=-0.02, trade_pnl=-20.0)
    assert store2.get_strategy(_SID).size_multiplier == 0.5


async def test_runner_seeds_multiplier_from_store_on_startup():
    _write_baseline()
    RiskStore("live").upsert_strategy(_SID, enabled=True, stage=LadderStage.MICRO,
                                      size_multiplier=0.25)  # persisted by a prior run
    r = _runner(Mode.LIVE)
    await r.startup()
    assert r._decay._multiplier[_SID] == 0.25


# ----------------------------------------------------------------------- L-E stale handoff
class _StaleSpy:
    def __init__(self, open_positions: int) -> None:
        self._entries_blocked = False
        self._open = open_positions
        self.block_reasons: list[str] = []
        self.stale_calls: list[datetime] = []

    def block_entries(self, reason: str) -> None:
        self._entries_blocked = True
        self.block_reasons.append(reason)

    def open_position_count(self) -> int:
        return self._open

    async def on_feed_stale(self, now: datetime) -> None:
        self.stale_calls.append(now)


async def test_stale_feed_with_open_positions_calls_on_feed_stale_once_per_stall():
    rth = [b for b in dataset(["AAAA"], n_sessions=1)["AAAA"] if b.minute_of_day >= 600]
    t0 = rth[10].ts
    r = _runner(Mode.SHADOW)
    spy = _StaleSpy(open_positions=1)
    r.executor = spy
    r._last_arrival = t0

    await r._check_staleness(t0 + timedelta(seconds=200))
    await r._check_staleness(t0 + timedelta(seconds=260))  # still stale, same stall
    assert len(spy.stale_calls) == 1, "on_feed_stale must fire once per stall"
    assert spy._entries_blocked

    # A fresh bar re-arms the one-shot; a subsequent stall fires again.
    r._stale_handled = False
    await r._check_staleness(t0 + timedelta(seconds=320))
    assert len(spy.stale_calls) == 2


async def test_stale_feed_without_open_positions_does_not_call_on_feed_stale():
    rth = [b for b in dataset(["AAAA"], n_sessions=1)["AAAA"] if b.minute_of_day >= 600]
    t0 = rth[10].ts
    r = _runner(Mode.SHADOW)
    spy = _StaleSpy(open_positions=0)
    r.executor = spy
    r._last_arrival = t0
    await r._check_staleness(t0 + timedelta(seconds=200))
    assert spy.stale_calls == []
    assert spy._entries_blocked  # entries are still blocked on staleness


# ----------------------------------------------------------------------- L-C adapter guardrail
async def test_runner_refuses_live_capable_adapter_in_paper():
    r = _runner(Mode.PAPER, adapter=_LiveSim("acct", DEFAULT_COST_MODEL, 100_000.0))
    with pytest.raises(AdapterModeMismatchError):
        r._check_adapters()


async def test_runner_refuses_paper_adapter_in_live():
    r = _runner(Mode.LIVE, adapter=SimBroker("acct", DEFAULT_COST_MODEL, 100_000.0))
    with pytest.raises(AdapterModeMismatchError):
        r._check_adapters()


# ----------------------------------------------------------------------- helpers
def _sink():
    from sgt.engine.eventlog import InMemoryEventSink
    return InMemoryEventSink()


class _null:
    def send(self, title: str, body: str) -> None:
        return None
