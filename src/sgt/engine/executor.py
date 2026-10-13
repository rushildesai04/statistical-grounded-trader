"""Executors: drive one :class:`StrategyEngine` against a broker + risk gate.

``Executor`` is the Protocol the backtest driver (and a future async
``LiveExecutor`` in ``sgt.live``) implement. All order-lifecycle logic
(protective stops, exit retry, entry timeout, session-end forced exit, the
open-position invariant) lives in the broker-agnostic
:class:`sgt.engine.orders.OrderLifecycle`. ``BacktestExecutor`` is a thin adapter:
it feeds SimBroker events (fills, terminal order states) into the lifecycle,
executes the pure actions the lifecycle emits (``submit_sync`` / ``cancel_sync``),
applies the risk gate to each proposed order, and keeps the trade journal.

Per-bar order of operations (plan §8):
  1. session roll (``sim.end_session`` + ``risk.start_session``) -> force-exit any
     position that crossed the boundary; feed newly-terminal orders to the lifecycle.
  2. ``sim.on_bar(bar)`` -> fills -> risk/engine/exposure/journal + lifecycle (stops).
  3. entry-timeout tick.
  4. ``engine.on_bar(bar)`` -> decisions -> lifecycle -> risk-checked submits.
  5. excursions + open-position invariant.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Mapping, Protocol, runtime_checkable

from sgt.brokers.costs import CostModel
from sgt.brokers.sim import SimBroker
from sgt.core.interfaces import EventSink, TradeContext
from sgt.core.types import (
    Bar,
    Fill,
    HaltEvent,
    HaltState,
    Mode,
    OrderAck,
    OrderIntent,
    RiskVerdict,
    Side,
)
from sgt.engine.engine import StrategyEngine
from sgt.engine.orders import Action, BrokerCaps, CancelAction, OrderLifecycle, SubmitAction
from sgt.risk.exposure import DictExposureView
from sgt.risk.limits import LadderStage, RiskConfig
from sgt.risk.manager import RiskManager
from sgt.risk.state import InMemoryRiskStore
from sgt.strategy.base import Candidate
from sgt.strategy.schema import StrategyConfig

_WINDOW_PRIORITY = ("power_hour", "after_hours", "post", "rth", "opening_hour", "midday", "pre")


@runtime_checkable
class Executor(Protocol):
    def process_bar(self, bar: Bar) -> None: ...
    def process_halt(self, event: HaltEvent) -> None: ...


@dataclass(slots=True)
class _OpenTrade:
    symbol: str
    entry_ts: datetime
    qty: int = 0
    entry_price_sum: float = 0.0
    entry_fees: float = 0.0
    entry_avg: float = 0.0
    exit_qty: int = 0
    exit_proceeds: float = 0.0
    exit_ts: datetime | None = None
    exit_reason: str = ""
    mfe: float = 0.0
    mae: float = 0.0
    bars_held: int = 0
    halted_during: bool = False


class BacktestExecutor(Executor):
    def __init__(
        self,
        config: StrategyConfig,
        engine: StrategyEngine,
        *,
        risk_config: RiskConfig,
        cost_model: CostModel,
        starting_cash: float,
        event_sink: EventSink,
        candidate_sink,
        run_id: str,
        config_hash: str,
        account_id: str = "backtest",
        entry_timeout_bars: int = 3,
        strict_invariants: bool = False,
    ) -> None:
        self.config = config
        self.engine = engine
        self.cost_model = cost_model
        self.sink = event_sink
        self._candidate_sink = candidate_sink
        self.run_id = run_id
        self.config_hash = config_hash
        self.account_id = account_id
        self.strategy_id = config.id
        self.strict_invariants = strict_invariants

        self.sim = SimBroker(account_id, cost_model, starting_cash)
        self.exposure = DictExposureView()
        store = InMemoryRiskStore(namespace=run_id)
        self.risk = RiskManager(risk_config, self.exposure, store=store, mode=Mode.BACKTEST)
        self.risk.arm(actor="backtest", reason=f"backtest {run_id}")
        store.upsert_strategy(self.strategy_id, enabled=True, stage=LadderStage.STANDARD)

        self.lifecycle = OrderLifecycle(
            config, account_id=account_id, strategy_id=self.strategy_id, config_hash=config_hash,
            caps=BrokerCaps(self.sim.supports_extended_hours, self.sim.supports_broker_side_stops),
            runtime_for=engine.runtime_for, entry_timeout_bars=entry_timeout_bars)

        self.trades: list[dict] = []
        self._open: dict[str, _OpenTrade] = {}
        self._pos_qty: dict[str, int] = {}
        self._order_reason: dict[str, str] = {}
        self._halted: set[str] = set()
        self._session: date | None = None
        self._sym_session: dict[str, date] = {}
        self._last_bar: dict[str, Bar] = {}
        self._flatten_journaled = False

    # ------------------------------------------------------------------ Executor API
    def process_bar(self, bar: Bar) -> None:
        # Settlement/expiry first (no order submissions), then process this bar's fills.
        self._last_bar[bar.symbol] = bar
        rolled = self._roll_session(bar)
        for fill in self.sim.on_bar(bar):
            self._handle_fill(fill, bar)
            self._run(self.lifecycle.on_fill(fill, bar), bar)
        # Every NEW order is submitted only AFTER sim.on_bar(bar): the SimBroker makes it
        # eligible no earlier than the next bar, preserving the no-look-ahead invariant.
        if rolled:
            self._run(self.lifecycle.on_session_roll(bar.symbol, bar), bar)
        self._feed_terminal_orders(bar)
        self._run(self.lifecycle.on_bar_tick(bar), bar)
        for decision in self.engine.on_bar(bar):
            self._run(self.lifecycle.on_decision(decision, bar), bar, source=decision)
        self._update_excursions(bar)
        self._mark_and_maybe_flatten(bar)
        for msg in self.lifecycle.check_invariant(bar, halted=bar.symbol in self._halted):
            self.sink.emit("invariant_violation", bar.ts, {"symbol": bar.symbol, "detail": msg})
            if self.strict_invariants:
                raise AssertionError(msg)

    def _mark_and_maybe_flatten(self, bar: Bar) -> None:
        """Mark the held position in ``bar.symbol`` to this bar's close for the daily
        realized+unrealized loss view, then flatten every position in a breached scope
        (plan §11.6). Latched protective exits; one ``risk_flatten`` journal entry."""
        if self.lifecycle.position_qty(bar.symbol) > 0:
            self.risk.mark_to_market(self.account_id, bar.symbol, bar.close, bar.ts)
        scopes = self.risk.flatten_scopes()
        if not scopes:
            return
        if not ("global" in scopes or f"account:{self.account_id}" in scopes
                or f"strategy:{self.strategy_id}" in scopes):
            return
        for sym in sorted(self.lifecycle.held_symbols()):
            sbar = self._last_bar.get(sym)
            if sbar is None:
                continue
            actions = self.lifecycle.flatten(sym, sbar, "risk_flatten")
            if actions:
                if not self._flatten_journaled:
                    self._flatten_journaled = True
                    self.sink.emit("risk_flatten", bar.ts, {"scopes": sorted(scopes)})
                self._run(actions, sbar)

    def process_halt(self, event: HaltEvent) -> None:
        self.sim.on_halt(event)
        self.engine.on_halt(event)
        if event.state is HaltState.HALTED:
            self._halted.add(event.symbol)
            if event.symbol in self._open:
                self._open[event.symbol].halted_during = True
        else:
            self._halted.discard(event.symbol)

    # ------------------------------------------------------------------ session
    def _roll_session(self, bar: Bar) -> bool:
        if bar.session_date != self._session:
            if self._session is not None:
                self.sim.end_session(self._session)
            self.risk.start_session(bar.session_date, {self.account_id: self.sim.cash})
            self._session = bar.session_date
            self._flatten_journaled = False  # one risk_flatten summary per session (PASS 3 F-2)
        prev = self._sym_session.get(bar.symbol)
        self._sym_session[bar.symbol] = bar.session_date
        return prev is not None and prev != bar.session_date

    def _feed_terminal_orders(self, bar: Bar) -> None:
        # Surface terminal order states as explicit OrderAck events for the
        # broker-agnostic lifecycle (live adapters supply these from their order stream).
        for cid in self.lifecycle.open_order_ids():
            state = self.sim.order_state_sync(cid)
            if state is not None and state.status.terminal:
                ack = OrderAck(cid, state.broker_order_id, state.status, bar.ts, symbol=state.symbol,
                               side=state.side, remaining_qty=state.remaining_qty)
                self._run(self.lifecycle.on_order_terminal(ack, bar), bar)

    # ------------------------------------------------------------------ action execution
    def _run(self, actions: list[Action], bar: Bar, source=None) -> None:
        for a in actions:
            if isinstance(a, CancelAction):
                self.sim.cancel_sync(a.client_order_id)
                self.sink.emit("cancel", bar.ts, {"client_order_id": a.client_order_id,
                                                  "symbol": a.symbol, "reason": a.reason})
            elif isinstance(a, SubmitAction):
                self._submit(a.intent, bar, source)

    def _submit(self, intent: OrderIntent, bar: Bar, source) -> None:
        is_entry = intent.side is Side.BUY
        ctx = self._trade_ctx(bar, intent.symbol, stop_price=intent.stop_price if is_entry else None)
        rc = self.risk.check(intent, ctx)
        if rc.verdict is RiskVerdict.REJECT or (is_entry and rc.qty <= 0):
            self.sink.emit("risk_reject", bar.ts,
                           {"client_order_id": intent.client_order_id, "symbol": intent.symbol,
                            "reasons": list(rc.reasons)})
            if is_entry and source is not None:
                self._candidate_sink(Candidate(source.signal_ts, intent.symbol, False, "risk",
                                               dict(source.features)))
            self._run(self.lifecycle.on_order_rejected(intent.client_order_id, intent.symbol, bar), bar)
            return
        if is_entry and rc.qty != intent.qty:
            intent = replace(intent, qty=rc.qty)
        if is_entry and source is not None:
            self._candidate_sink(Candidate(source.signal_ts, intent.symbol, True, None,
                                           dict(source.features)))
        self.sim.submit_sync(intent)
        self._order_reason[intent.client_order_id] = intent.reason
        self.sink.emit("order", bar.ts, _order_payload(intent))

    # ------------------------------------------------------------------ fills / trades
    def _handle_fill(self, fill: Fill, bar: Bar) -> None:
        self.risk.on_fill(fill)
        self.engine.on_fill(fill)
        sym = fill.symbol
        self.sink.emit("fill", fill.ts, {
            "fill_id": fill.fill_id, "client_order_id": fill.client_order_id, "symbol": sym,
            "side": fill.side.value, "qty": fill.qty, "price": fill.price, "fee": fill.fee})
        qty = self._pos_qty.get(sym, 0)
        if fill.side is Side.BUY:
            qty += fill.qty
            tr = self._open.get(sym)
            if tr is None:
                tr = _OpenTrade(symbol=sym, entry_ts=fill.ts)
                self._open[sym] = tr
            tr.qty += fill.qty
            tr.entry_price_sum += fill.qty * fill.price
            tr.entry_fees += fill.fee
            tr.entry_avg = tr.entry_price_sum / tr.qty
        else:
            qty -= fill.qty
            tr = self._open.get(sym)
            if tr is not None:
                tr.exit_qty += fill.qty
                tr.exit_proceeds += fill.qty * fill.price - fill.fee
                tr.exit_ts = fill.ts
                tr.exit_reason = self._order_reason.get(fill.client_order_id, tr.exit_reason or "exit")
        self._pos_qty[sym] = max(qty, 0)
        self.exposure.set_position(self.account_id, sym, self._pos_qty[sym], mark=fill.price)
        if self._pos_qty[sym] <= 0 and sym in self._open:
            self._finalize_trade(sym)

    def _update_excursions(self, bar: Bar) -> None:
        tr = self._open.get(bar.symbol)
        if tr is None or tr.entry_avg <= 0:
            return
        tr.mfe = max(tr.mfe, (bar.high - tr.entry_avg) / tr.entry_avg)
        tr.mae = min(tr.mae, (bar.low - tr.entry_avg) / tr.entry_avg)
        tr.bars_held += 1
        if bar.symbol in self._halted:
            tr.halted_during = True

    def _finalize_trade(self, sym: str) -> None:
        tr = self._open.pop(sym)
        cost = tr.entry_price_sum + tr.entry_fees
        pnl = tr.exit_proceeds - cost
        self.trades.append({
            "symbol": sym, "entry_ts": tr.entry_ts, "entry_price": tr.entry_avg,
            "exit_ts": tr.exit_ts, "exit_price": (tr.exit_proceeds / tr.exit_qty) if tr.exit_qty else None,
            "qty": tr.exit_qty or tr.qty, "pnl": pnl, "ret": (pnl / cost) if cost else 0.0,
            "exit_reason": tr.exit_reason.replace("exit:", ""), "mfe": tr.mfe, "mae": tr.mae,
            "bars_held": tr.bars_held, "halted_during": tr.halted_during})
        self._pos_qty[sym] = 0

    def close_open_positions(self, last_bars: Mapping[str, Bar]) -> None:
        """Flatten every still-open position at its symbol's last bar close
        (explicit ``end_of_data`` exits so no PnL is silently dropped)."""
        for sym in list(self._open):
            bar = last_bars.get(sym)
            tr = self._open[sym]
            if bar is None:
                self._open.pop(sym)
                continue
            fee = self.cost_model.fees(tr.qty) if tr.qty > 0 else 0.0
            tr.exit_qty = tr.qty
            tr.exit_proceeds = tr.qty * bar.close - fee
            tr.exit_ts = bar.ts
            tr.exit_reason = "end_of_data"
            self._pos_qty[sym] = 0
            self._finalize_trade(sym)

    # ------------------------------------------------------------------ helpers
    def _trade_ctx(self, bar: Bar, sym: str, *, stop_price: float | None) -> TradeContext:
        return TradeContext(
            now=bar.ts, mark_price=bar.close, session_window=self._window(bar),
            halted=sym in self._halted, spread_pct=None, luld_distance_pct=None,
            stop_price=stop_price, bar_volume=bar.volume)

    def _window(self, bar: Bar) -> str:
        wins = self.engine.calendar.windows(bar.ts)
        for w in _WINDOW_PRIORITY:
            if w in wins:
                return w
        return "closed"


def _order_payload(intent: OrderIntent) -> dict:
    return {"client_order_id": intent.client_order_id, "symbol": intent.symbol,
            "side": intent.side.value, "qty": intent.qty, "order_type": intent.order_type.value,
            "limit_price": intent.limit_price, "stop_price": intent.stop_price, "reason": intent.reason}
