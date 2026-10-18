"""Replay-equals-live parity harness (plan §3.1).

``replay_session`` drives :class:`~sgt.live.executor.LiveExecutor` in SHADOW mode over
recorded bars with an in-memory :class:`~sgt.core.interfaces.LiveBarSource` and a
:class:`~sgt.brokers.sim.SimBroker`, using the SAME account id, cost model, risk config,
strategy engine and merge order as :func:`sgt.engine.backtest.run_backtest`. Because the
engine and the order lifecycle are shared code and every id is content-derived, the
shadow run must produce the identical sequence of decisions, orders and fills — this is
the plan's "replay equals live" guarantee. ``assert_replay_parity`` checks it directly
against a backtest event log (modulo wall-clock-only fields, of which there are none here
since every timestamp is bar-derived).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, AsyncIterator, Mapping, Sequence

from sgt.brokers.costs import DEFAULT_COST_MODEL, CostModel
from sgt.brokers.sim import SimBroker
from sgt.core.interfaces import EventSink, LiveBarSource
from sgt.core.types import Bar, HaltEvent, Mode
from sgt.engine.backtest import default_risk_config
from sgt.engine.engine import StrategyEngine
from sgt.engine.eventlog import InMemoryEventSink
from sgt.live.alerts import AlertManager
from sgt.live.executor import LiveExecutor
from sgt.brokers.alert_only import LogNotifier
from sgt.risk.exposure import DictExposureView
from sgt.risk.limits import LadderStage, RiskConfig
from sgt.risk.manager import RiskManager
from sgt.risk.state import InMemoryRiskStore
from sgt.strategy.base import Candidate
from sgt.strategy.loader import config_hash as compute_config_hash
from sgt.strategy.runtime import EntryOverride
from sgt.strategy.schema import StrategyConfig

_SHADOW_ACCOUNT = "backtest"  # identical to run_backtest so client_order_ids match
_PARITY_KINDS = ("order", "fill", "cancel", "risk_reject")


class InMemoryLiveSource(LiveBarSource):
    """A :class:`LiveBarSource` that replays a fixed, time-ordered list of bars."""

    name = "in_memory"

    def __init__(self, bars: Sequence[Bar], *, delay_minutes: int = 0,
                 consolidated: bool = True) -> None:
        self._bars = list(bars)
        self.delay_minutes = delay_minutes
        self.consolidated = consolidated

    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[Bar]:
        wanted = {s.upper() for s in symbols}
        for bar in self._bars:
            if bar.symbol in wanted:
                yield bar

    async def close(self) -> None:
        return None


class ShadowResult:
    def __init__(self, trades: list[dict], candidates: list[Candidate],
                 events: EventSink, config_hash: str, run_id: str) -> None:
        self.trades = trades
        self.candidates = candidates
        self.events = events
        self.config_hash = config_hash
        self.run_id = run_id


def _merge(bars_by_symbol: Mapping[str, Sequence[Bar]],
           halts: Sequence[HaltEvent]) -> list[tuple[str, datetime, Any]]:
    # Identical total order to run_backtest: (ts, 0=halt before 1=bar, symbol).
    events: list[tuple[datetime, int, str, str, Any]] = []
    for sym, bars in bars_by_symbol.items():
        for b in bars:
            events.append((b.ts, 1, sym, "bar", b))
    for h in halts:
        events.append((h.ts, 0, h.symbol, "halt", h))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    return [(kind, ts, item) for ts, _rank, _sym, kind, item in events]


async def replay_session(
    config: StrategyConfig,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    halts: Sequence[HaltEvent] = (),
    reference: object = None,
    meta_filter: Any = None,
    entry_override: EntryOverride | None = None,
    cost_model: CostModel | None = None,
    starting_cash: float,
    event_sink: EventSink | None = None,
    run_id: str = "shadow",
    risk_config: RiskConfig | None = None,
    entry_timeout_bars: int = 3,
    strict_invariants: bool = False,
    allow_unknown_float: bool = False,
    policy: object = None,
) -> ShadowResult:
    cost_model = cost_model or DEFAULT_COST_MODEL
    sink = event_sink if event_sink is not None else InMemoryEventSink()
    cfg_hash = compute_config_hash(config)
    candidates: list[Candidate] = []

    def candidate_sink(c: Candidate) -> None:
        candidates.append(c)
        sink.emit("candidate", c.ts, {"symbol": c.symbol, "taken": c.taken,
                                      "skip_reason": c.skip_reason or ""})

    meta_scorer = meta_filter.score if meta_filter is not None else None
    engine = StrategyEngine(config, reference=reference, meta_scorer=meta_scorer,
                            entry_override=entry_override, candidate_sink=candidate_sink,
                            allow_unknown_float=allow_unknown_float, policy=policy)

    sim = SimBroker(_SHADOW_ACCOUNT, cost_model, starting_cash)
    exposure = DictExposureView()
    store = InMemoryRiskStore(namespace=run_id)
    risk = RiskManager(risk_config or default_risk_config(config), exposure,
                       store=store, mode=Mode.SHADOW)
    risk.arm(actor="replay", reason=f"shadow replay {run_id}")
    store.upsert_strategy(config.id, enabled=True, stage=LadderStage.STANDARD)

    executor = LiveExecutor(
        config, engine, risk=risk, adapters={_SHADOW_ACCOUNT: sim}, exposure=exposure,
        cost_model=cost_model, event_sink=sink, candidate_sink=candidate_sink,
        alerts=AlertManager(LogNotifier()), run_id=run_id, config_hash=cfg_hash,
        mode=Mode.SHADOW, entry_timeout_bars=entry_timeout_bars,
        strict_invariants=strict_invariants)

    last_bar: dict[str, Bar] = {}
    for kind, _ts, item in _merge(bars_by_symbol, halts):
        if kind == "halt":
            executor.process_halt(item)
        else:
            await executor.process_bar(item)
            last_bar[item.symbol] = item
    executor.close_open_positions(last_bar)
    sink.flush()
    return ShadowResult(executor.trades, candidates, sink, cfg_hash, run_id)


def parity_events(sink: EventSink, kinds: Sequence[str] = _PARITY_KINDS) -> list[tuple]:
    """The decision/order/fill event signature used for parity comparison."""
    keep = set(kinds)
    return [(e["kind"], e["ts"], e["payload"]) for e in sink.events if e["kind"] in keep]


def assert_replay_parity(backtest_sink: EventSink, shadow_sink: EventSink,
                         kinds: Sequence[str] = _PARITY_KINDS) -> None:
    bt = parity_events(backtest_sink, kinds)
    sh = parity_events(shadow_sink, kinds)
    if bt != sh:
        first = next((i for i in range(min(len(bt), len(sh))) if bt[i] != sh[i]), None)
        detail = f"lengths bt={len(bt)} shadow={len(sh)}"
        if first is not None:
            detail = f"first divergence at {first}: backtest={bt[first]} shadow={sh[first]}"
        raise AssertionError(f"replay parity broken: {detail}")
