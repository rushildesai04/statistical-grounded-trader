"""Shared fakes + builders for the live-runtime tests (no network, deterministic)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import AsyncIterator, Callable

from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.brokers.sim import SimBroker, _Lot, _Working
from sgt.core.types import (
    OrderIntent,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from sgt.core.interfaces import (
    AdapterHealth,
    BrokerAdapter,
    BrokerAuthError,
    BrokerUnavailableError,
)
from sgt.core.types import AccountState, Bar, Fill, HaltEvent, Mode, OrderAck, Segment, Side
from sgt.engine.backtest import default_risk_config
from sgt.engine.engine import StrategyEngine
from sgt.engine.eventlog import InMemoryEventSink
from sgt.intraday.calendar import default_calendar
from sgt.intraday.synthetic import Ignition, make_multi_session
from sgt.live.alerts import AlertManager
from sgt.live.executor import LiveExecutor
from sgt.brokers.alert_only import LogNotifier, Notifier
from sgt.risk.exposure import DictExposureView
from sgt.risk.manager import RiskManager
from sgt.risk.limits import LadderStage, RiskConfig
from sgt.risk.state import InMemoryRiskStore
from sgt.strategy.base import Candidate
from sgt.strategy.loader import config_hash as compute_config_hash, load_strategy
from sgt.strategy.runtime import EntryOverride

CFG = load_strategy("tests/fixtures/trader_v0_mechanics.yaml")


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send(self, title: str, body: str) -> None:
        self.sent.append((title, body))


class FaultSimBroker(SimBroker):
    """SimBroker that injects faults on ``submit`` to exercise retry/auth handling.

    ``unavailable_times`` submit attempts raise BrokerUnavailableError before the call
    is allowed through; ``auth_fail`` makes every submit raise BrokerAuthError.
    ``distinct_orders`` counts unique client_order_ids that actually reached the sim."""

    def __init__(self, *args, unavailable_times: int = 0, auth_fail: bool = False, **kw) -> None:
        super().__init__(*args, **kw)
        self._unavailable_times = unavailable_times
        self._auth_fail = auth_fail
        self.submit_attempts = 0

    async def submit(self, intent) -> OrderAck:
        self.submit_attempts += 1
        if self._auth_fail:
            raise BrokerAuthError("fake auth failure")
        if self._unavailable_times > 0:
            self._unavailable_times -= 1
            raise BrokerUnavailableError("fake transient failure")
        return self.submit_sync(intent)

    @property
    def distinct_orders(self) -> int:
        return len(self._orders)


class DupFillBroker(SimBroker):
    """SimBroker that re-emits its first fill on the next ``on_bar`` call (duplicate
    fill_id) so the executor's dedupe can be exercised."""

    def __init__(self, *args, **kw) -> None:
        super().__init__(*args, **kw)
        self._dup_pending: Fill | None = None
        self._dup_done = False

    def on_bar(self, bar: Bar) -> list[Fill]:
        fills = super().on_bar(bar)
        if self._dup_pending is not None and not self._dup_done:
            self._dup_done = True
            out = [self._dup_pending] + fills
            self._dup_pending = None
            return out
        if fills and not self._dup_done and self._dup_pending is None:
            self._dup_pending = fills[0]
        return fills


def dataset(symbols, *, seed=1, n_sessions=2):
    sess = default_calendar().previous_sessions(date(2024, 3, 15), n_sessions)
    igs = (Ignition(start_minute=70, length=15, drift_per_bar=0.02, volume_mult=12.0),)
    out = {}
    for i, sym in enumerate(symbols):
        out[sym] = make_multi_session(sym, sess, seed=seed + i, start_price=6.0 + i,
                                      segments=(Segment.RTH,), ignitions=igs)
    return out


def build_executor(
    *,
    adapter: BrokerAdapter | None = None,
    account_id: str = "acct",
    mode: Mode = Mode.SHADOW,
    arm: bool = True,
    entry_override: EntryOverride | None = None,
    risk_config: RiskConfig | None = None,
    starting_cash: float = 100_000.0,
    stage: LadderStage = LadderStage.STANDARD,
    register: bool = True,
    on_closed_trade: Callable[[dict], None] | None = None,
    notifier: Notifier | None = None,
    submit_retries: int = 3,
):
    sink = InMemoryEventSink()
    candidates: list[Candidate] = []

    def candidate_sink(c: Candidate) -> None:
        candidates.append(c)

    engine = StrategyEngine(CFG, entry_override=entry_override, candidate_sink=candidate_sink, allow_unknown_float=True)
    adapter = adapter or SimBroker(account_id, DEFAULT_COST_MODEL, starting_cash)
    exposure = DictExposureView()
    store = InMemoryRiskStore(namespace=f"{mode.value}-test")
    risk = RiskManager(risk_config or default_risk_config(CFG), exposure, store=store, mode=mode)
    if arm:
        risk.arm(actor="test", reason="test")
    if register:
        store.upsert_strategy(CFG.id, enabled=True, stage=stage)
    alerts = AlertManager(notifier or LogNotifier())
    ex = LiveExecutor(
        CFG, engine, risk=risk, adapters={account_id: adapter}, exposure=exposure,
        cost_model=DEFAULT_COST_MODEL, event_sink=sink, candidate_sink=candidate_sink,
        alerts=alerts, run_id=f"{mode.value}-test", config_hash=compute_config_hash(CFG),
        mode=mode, on_closed_trade=on_closed_trade, submit_retries=submit_retries,
        op_timeout_s=5.0, sleep=_noop_sleep)
    return _Harness(ex, risk, sink, exposure, adapter, candidates, alerts, store)


async def _noop_sleep(_s: float) -> None:
    return None


class _Harness:
    def __init__(self, executor, risk, sink, exposure, adapter, candidates, alerts, store):
        self.executor = executor
        self.risk = risk
        self.sink = sink
        self.exposure = exposure
        self.adapter = adapter
        self.candidates = candidates
        self.alerts = alerts
        self.store = store

    async def drive(self, bars_by_symbol, halts=()):
        merged: list = []
        for sym, bars in bars_by_symbol.items():
            for b in bars:
                merged.append((b.ts, 1, sym, "bar", b))
        for h in halts:
            merged.append((h.ts, 0, h.symbol, "halt", h))
        merged.sort(key=lambda e: (e[0], e[1], e[2]))
        last: dict[str, Bar] = {}
        for _ts, _r, _s, kind, item in merged:
            if kind == "halt":
                self.executor.process_halt(item)
            else:
                await self.executor.process_bar(item)
                last[item.symbol] = item
        return last

    def order_events(self, side: str | None = None) -> list[dict]:
        out = [e["payload"] for e in self.sink.events if e["kind"] == "order"]
        if side is not None:
            out = [p for p in out if p["side"] == side]
        return out

    def events(self, kind: str) -> list[dict]:
        return [e["payload"] for e in self.sink.events if e["kind"] == kind]


def sim_with_position(account_id="acct", *, symbol="AAAA", qty=100, avg=6.0,
                      starting_cash=100_000.0, with_stop=False, stop_price=5.8):
    """A SimBroker reporting a pre-existing position (and optionally a working broker
    protective SELL STOP), as a restart would see."""
    sim = SimBroker(account_id, DEFAULT_COST_MODEL, starting_cash)
    sim._positions[symbol] = _Lot(qty=qty, avg_price=avg)
    if with_stop:
        intent = OrderIntent(
            client_order_id="existing-stop-1", account_id=account_id, symbol=symbol,
            side=Side.SELL, qty=qty, order_type=OrderType.STOP, limit_price=None,
            tif=TimeInForce.GTC, extended_hours=False, strategy_id="x", reason="exit:stop",
            created_at=datetime(2024, 3, 12, 13, 0, tzinfo=timezone.utc), stop_price=stop_price)
        sim._orders[intent.client_order_id] = _Working(
            intent=intent, submit_seq=0, remaining=qty, status=OrderStatus.WORKING)
    return sim


def rth_bars(symbol, closes, *, lows=None, highs=None, vol=80_000.0,
             session=date(2024, 3, 12), start_minute=600):
    """Hand-built annotated RTH bars for a session (09:30 ET == 13:30 UTC, EDT)."""
    cal = default_calendar()
    base = datetime(session.year, session.month, session.day, 13, 30, tzinfo=timezone.utc)
    out = []
    for i, c in enumerate(closes):
        ts = base + timedelta(minutes=(start_minute - 570) + i)
        lo = lows[i] if lows is not None else c - 0.05
        hi = highs[i] if highs is not None else c + 0.05
        bar = cal.annotate(symbol, ts, c, max(hi, c), min(lo, c), c, vol)
        if bar is not None:
            out.append(bar)
    return out
