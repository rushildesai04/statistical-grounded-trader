"""Async executor for SHADOW / PAPER / LIVE (plan §3.1, §8).

``LiveExecutor`` is the live-mode counterpart of
:class:`sgt.engine.executor.BacktestExecutor`. It drives the SAME
:class:`~sgt.engine.engine.StrategyEngine` and reuses the broker-agnostic
:class:`~sgt.engine.orders.OrderLifecycle` UNCHANGED, one per account, so a live
decision is byte-for-byte the decision a backtest would make on the same bars. The
only differences are that every broker call is async, bounded and fault-handled (see
:mod:`sgt.live.broker_io`), and that fills arrive from broker streams (or, in SHADOW,
from a :class:`SimBroker` driven bar-by-bar) instead of a synchronous return. Position
and closed-trade accounting lives in :class:`sgt.live.trades.TradeBook`.

Per-bar order of operations is identical to the backtest driver:
  1. session roll (end_session on sim brokers + ``risk.start_session``);
  2. collect fills (sim ``on_bar`` and/or the async fill queue), dedupe by ``fill_id``,
     feed ``risk.on_fill`` + ``engine.on_fill`` + per-account ``OrderLifecycle.on_fill``;
  3. forced exits for positions that crossed a session boundary;
  4. terminal order states (polled from ``open_orders()``) -> lifecycle;
  5. entry-timeout tick;
  6. ``engine.on_bar`` decisions -> allocation -> per-account lifecycle -> risk-checked
     submits;
  7. excursions + the open-position invariant.

Failure handling is fail-closed: ``BrokerAuthError`` disarms the namespace, alerts and
stops new entries; ``BrokerUnavailableError`` on a submit retries with the SAME
deterministic ``client_order_id`` up to a bound; ``BrokerRejectedError`` routes to
``OrderLifecycle.on_order_rejected``. A stale feed blocks new entries at the decision
stage until the feed recovers; a reconciliation mismatch halts an account entirely.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Awaitable, Callable, Mapping, Sequence

from sgt.accounts.registry import AccountSpec
from sgt.brokers.costs import CostModel
from sgt.core.interfaces import (
    BrokerAdapter,
    BrokerAuthError,
    BrokerUnavailableError,
    EventSink,
    TradeContext,
)
from sgt.core.types import (
    Bar,
    Fill,
    HaltEvent,
    HaltState,
    Mode,
    OrderAck,
    OrderIntent,
    OrderStatus,
    RiskVerdict,
    Side,
)
from sgt.engine.engine import StrategyEngine
from sgt.engine.orders import Action, BrokerCaps, CancelAction, OrderLifecycle, SubmitAction
from sgt.live.alerts import AlertManager
from sgt.live.broker_io import BrokerIOMixin, order_payload
from sgt.live.trades import ClosedTradeHook, TradeBook
from sgt.risk.exposure import DictExposureView
from sgt.risk.manager import RiskManager
from sgt.strategy.base import Candidate
from sgt.strategy.schema import StrategyConfig

log = logging.getLogger(__name__)

_WINDOW_PRIORITY = ("power_hour", "after_hours", "post", "rth", "opening_hour", "midday", "pre")


def _largest_remainder(total: int, weights: Sequence[float]) -> list[int]:
    wsum = sum(weights)
    if wsum <= 0 or total <= 0:
        return [0] * len(weights)
    exact = [total * w / wsum for w in weights]
    floors = [int(x) for x in exact]
    rem = total - sum(floors)
    order = sorted(range(len(weights)), key=lambda i: (exact[i] - floors[i], weights[i]), reverse=True)
    for i in order[:rem]:
        floors[i] += 1
    return floors


def _utcnow_bar() -> datetime:
    return datetime.now(timezone.utc)


class LiveExecutor(BrokerIOMixin):
    def __init__(
        self,
        config: StrategyConfig,
        engine: StrategyEngine,
        *,
        risk: RiskManager,
        adapters: Mapping[str, BrokerAdapter],
        exposure: DictExposureView,
        cost_model: CostModel,
        event_sink: EventSink,
        candidate_sink: Callable[[Candidate], None],
        alerts: AlertManager,
        run_id: str,
        config_hash: str,
        mode: Mode,
        accounts: Sequence[AccountSpec] | None = None,
        entry_timeout_bars: int = 3,
        strict_invariants: bool = False,
        on_closed_trade: ClosedTradeHook | None = None,
        op_timeout_s: float = 5.0,
        submit_retries: int = 3,
        backoff_base_s: float = 0.2,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if not adapters:
            raise ValueError("LiveExecutor requires at least one broker adapter")
        self.config = config
        self.engine = engine
        self.risk = risk
        self.adapters = dict(adapters)
        self.exposure = exposure
        self.cost_model = cost_model
        self.sink = event_sink
        self._candidate_sink = candidate_sink
        self._alerts = alerts
        self.run_id = run_id
        self.config_hash = config_hash
        self.mode = mode
        self.strategy_id = config.id
        self.strict_invariants = strict_invariants
        self._op_timeout = op_timeout_s
        self._submit_retries = max(0, submit_retries)
        self._backoff_base = backoff_base_s
        self._sleep = sleep

        self._account_ids = sorted(self.adapters)
        self._specs = {a.account_id: a for a in (accounts or [])}
        self._weights = {aid: self._specs[aid].allocation_weight if aid in self._specs else 1.0
                         for aid in self._account_ids}
        self._lifecycles: dict[str, OrderLifecycle] = {}
        for aid in self._account_ids:
            ad = self.adapters[aid]
            self._lifecycles[aid] = OrderLifecycle(
                config, account_id=aid, strategy_id=self.strategy_id, config_hash=config_hash,
                caps=BrokerCaps(ad.supports_extended_hours, ad.supports_broker_side_stops),
                runtime_for=engine.runtime_for, entry_timeout_bars=entry_timeout_bars)

        self.book = TradeBook(strategy_id=self.strategy_id, exposure=exposure,
                              cost_model=cost_model, on_closed_trade=on_closed_trade)
        self._order_reason: dict[str, str] = {}
        self._order_account: dict[str, str] = {}
        # client_order_id -> (decision reference price, modeled per-share slippage) so a
        # closed trade can report realized-vs-modeled slippage to the decay monitors.
        self._order_ref: dict[str, tuple[float, float]] = {}
        self._halted: set[str] = set()
        self._session: date | None = None
        self._sym_session: dict[str, date] = {}
        self._last_bar: dict[str, Bar] = {}
        self._flatten_journaled = False
        self._feed_stale_alerted = False
        self._seen_fills: set[str] = set()
        self._queue: asyncio.Queue[tuple[str, Fill]] = asyncio.Queue()
        self._pollers: list[asyncio.Task] = []
        self._running = False
        self._entries_blocked = False
        self._block_reason = ""
        self._auth_failed: set[str] = set()
        self._halted_accounts: set[str] = set()

    # ------------------------------------------------------------------ helpers / views
    @property
    def trades(self) -> list[dict]:
        return self.book.trades

    def _bar_driven(self, aid: str) -> bool:
        return callable(getattr(self.adapters[aid], "on_bar", None))

    def _lc(self, aid: str) -> OrderLifecycle:
        return self._lifecycles[aid]

    def open_position_count(self) -> int:
        return self.book.open_position_count()

    def held_symbols(self) -> set[str]:
        return self.book.held_symbols()

    def seed_position(self, aid: str, symbol: str, qty: int, avg_price: float) -> None:
        """Seed a position reconstructed from broker state at startup so the risk gate and
        exposure view know it. The broker-side protective stop (which survives a restart by
        design) remains the protection of record; the lifecycle owns no public seed API, so
        startup verifies/alerts on the broker's own stop rather than re-deriving one."""
        self.book.seed_position(aid, symbol, qty, avg_price)

    def block_entries(self, reason: str) -> None:
        if not self._entries_blocked:
            self.sink.emit("entries_blocked", _utcnow_bar(), {"reason": reason})
        self._entries_blocked = True
        self._block_reason = reason

    def unblock_entries(self) -> None:
        if self._entries_blocked:
            self.sink.emit("entries_unblocked", _utcnow_bar(), {"reason": self._block_reason})
        self._entries_blocked = False
        self._block_reason = ""

    def halt_account(self, aid: str, reason: str) -> None:
        """Stop ALL broker activity for one account (reconciliation mismatch, fail closed).
        Existing broker-side protective stops are left in place; we place no new orders and
        issue no cancels for this account."""
        if aid in self._halted_accounts:
            return
        self._halted_accounts.add(aid)
        self.sink.emit("account_halted", _utcnow_bar(), {"account_id": aid, "reason": reason})
        self._alerts.alert(f"ACCOUNT HALTED {aid}", reason)

    # ------------------------------------------------------------------ run lifecycle
    async def ensure_session_started(self, session_date: date) -> None:
        """Start the risk session for ``session_date`` if not already started. Idempotent
        per date; the runner calls it at startup, ``process_bar`` on every session roll."""
        if session_date == self._session:
            return
        for aid in self._account_ids:
            if self._session is not None and self._bar_driven(aid):
                self.adapters[aid].end_session(self._session)
        equity = await self._equity_by_account()
        self.risk.start_session(session_date, equity)
        self._session = session_date
        self._flatten_journaled = False  # one risk_flatten summary per session (PASS 3 F-2)

    # ------------------------------------------------------------------ per bar
    async def process_bar(self, bar: Bar) -> None:
        await self.ensure_session_started(bar.session_date)
        # A bar arriving means the feed is live again: clear the stale-feed latch so the
        # next stall alerts exactly once (plan §11, L-E).
        self._feed_stale_alerted = False
        self._last_bar[bar.symbol] = bar
        rolled = self._mark_symbol_session(bar)
        for aid, fill in await self._collect_fills(bar):
            self._handle_fill(aid, fill, bar)
            await self._run(aid, self._lc(aid).on_fill(fill, bar), bar)
        if rolled:
            for aid in self._account_ids:
                await self._run(aid, self._lc(aid).on_session_roll(bar.symbol, bar), bar)
        await self._feed_terminal_orders(bar)
        for aid in self._account_ids:
            await self._run(aid, self._lc(aid).on_bar_tick(bar), bar)
        for decision in self.engine.on_bar(bar):
            await self._dispatch_decision(decision, bar)
        self.book.update_excursions(bar, self._halted)
        await self._mark_and_maybe_flatten(bar)
        for aid in self._account_ids:
            for msg in self._lc(aid).check_invariant(bar, halted=bar.symbol in self._halted):
                self.sink.emit("invariant_violation", bar.ts,
                               {"account_id": aid, "symbol": bar.symbol, "detail": msg})
                if self.strict_invariants:
                    raise AssertionError(msg)

    async def _mark_and_maybe_flatten(self, bar: Bar) -> None:
        """Mark every account's held position in ``bar.symbol`` to this bar's close, then
        flatten all held positions in any scope the risk layer reports as breached (plan
        §11.6). Latched protective exits; one ``risk_flatten`` journal entry."""
        for aid in self._account_ids:
            if self._lc(aid).position_qty(bar.symbol) > 0:
                self.risk.mark_to_market(aid, bar.symbol, bar.close, bar.ts)
        scopes = self.risk.flatten_scopes()
        if not scopes:
            return
        strat_scope = f"strategy:{self.strategy_id}" in scopes
        for aid in self._account_ids:
            if aid in self._halted_accounts:
                continue
            if not ("global" in scopes or f"account:{aid}" in scopes or strat_scope):
                continue
            lc = self._lc(aid)
            for sym in sorted(lc.held_symbols()):
                sbar = self._last_bar.get(sym)
                if sbar is None:
                    continue
                actions = lc.flatten(sym, sbar, "risk_flatten")
                if actions:
                    if not self._flatten_journaled:
                        self._flatten_journaled = True
                        self.sink.emit("risk_flatten", bar.ts, {"scopes": sorted(scopes)})
                    await self._run(aid, actions, sbar)

    async def on_feed_stale(self, now: datetime) -> None:
        """Called by the runner when the market-data feed is stale while positions are
        open (L-E). Raise one critical alert per stall and make sure every held position
        still has a working broker-side protective stop where the segment supports one,
        re-placing any that went missing."""
        held = {aid: self._lc(aid).held_symbols() for aid in self._account_ids}
        if not any(held.values()):
            return
        if not self._feed_stale_alerted:
            self._feed_stale_alerted = True
            open_syms = sorted({s for syms in held.values() for s in syms})
            self._alerts.alert(
                "CRITICAL: FEED STALE WITH OPEN POSITIONS",
                f"stale at {now.isoformat()}; verifying protective stops for {open_syms}")
        for aid, syms in held.items():
            if aid in self._halted_accounts:
                continue
            lc = self._lc(aid)
            for sym in syms:
                sbar = self._last_bar.get(sym)
                if sbar is None:
                    continue
                actions = lc.ensure_protective_stop(sym, sbar)
                if actions:
                    await self._run(aid, actions, sbar)

    def process_halt(self, event: HaltEvent) -> None:
        for aid in self._account_ids:
            ad = self.adapters[aid]
            if callable(getattr(ad, "on_halt", None)):
                ad.on_halt(event)
        self.engine.on_halt(event)
        if event.state is HaltState.HALTED:
            self._halted.add(event.symbol)
            self.book.mark_halted(event.symbol)
        else:
            self._halted.discard(event.symbol)

    def close_open_positions(self, last_bars: Mapping[str, Bar]) -> None:
        self.book.close_open_positions(last_bars)

    # ------------------------------------------------------------------ session helpers
    def _mark_symbol_session(self, bar: Bar) -> bool:
        prev = self._sym_session.get(bar.symbol)
        self._sym_session[bar.symbol] = bar.session_date
        return prev is not None and prev != bar.session_date

    async def _equity_by_account(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for aid in self._account_ids:
            try:
                state = await self._bounded(lambda a=aid: self.adapters[a].account_state(),
                                            "account_state")
                out[aid] = float(state.cash)
            except (BrokerUnavailableError, BrokerAuthError):
                out[aid] = 0.0
        return out

    # ------------------------------------------------------------------ fills
    async def _collect_fills(self, bar: Bar) -> list[tuple[str, Fill]]:
        out: list[tuple[str, Fill]] = []
        for aid in self._account_ids:
            if self._bar_driven(aid):
                for fill in self.adapters[aid].on_bar(bar):
                    if fill.fill_id not in self._seen_fills:
                        self._seen_fills.add(fill.fill_id)
                        out.append((aid, fill))
        while not self._queue.empty():
            aid, fill = self._queue.get_nowait()
            if fill.fill_id not in self._seen_fills:
                self._seen_fills.add(fill.fill_id)
                out.append((aid, fill))
        return out

    def _handle_fill(self, aid: str, fill: Fill, bar: Bar) -> None:
        self.risk.on_fill(fill)
        self.engine.on_fill(fill)
        self.sink.emit("fill", fill.ts, {
            "fill_id": fill.fill_id, "client_order_id": fill.client_order_id, "symbol": fill.symbol,
            "side": fill.side.value, "qty": fill.qty, "price": fill.price, "fee": fill.fee})
        ref, modeled_ps = self._order_ref.get(fill.client_order_id, (fill.price, 0.0))
        realized_slip = abs(fill.price - ref) * fill.qty
        modeled_slip = modeled_ps * fill.qty
        self.book.record_fill(aid, fill, self._order_reason,
                              modeled_slip=modeled_slip, realized_slip=realized_slip)

    # ------------------------------------------------------------------ terminal orders
    async def _feed_terminal_orders(self, bar: Bar) -> None:
        for aid in self._account_ids:
            open_ids = self._lc(aid).open_order_ids()
            if not open_ids:
                continue
            try:
                acks = await self._bounded(lambda a=aid: self.adapters[a].open_orders(), "open_orders")
            except BrokerAuthError as exc:
                self._on_auth_error(aid, exc)
                continue
            except BrokerUnavailableError as exc:
                self._alerts.alert(f"OPEN ORDERS UNAVAILABLE {aid}", str(exc))
                continue
            working = {a.client_order_id for a in acks if not a.status.terminal}
            for cid in open_ids:
                if cid not in working:
                    ack = OrderAck(cid, "", OrderStatus.CANCELED, bar.ts, "terminal_by_poll")
                    await self._run(aid, self._lc(aid).on_order_terminal(ack, bar), bar)

    # ------------------------------------------------------------------ decisions / submit
    async def _dispatch_decision(self, decision, bar: Bar) -> None:
        if decision.kind == "entry" and self._entries_blocked:
            rt = self.engine.runtime_for(decision.symbol)
            if rt is not None:
                rt.reset_entry()
            self.sink.emit("entry_blocked", bar.ts,
                           {"symbol": decision.symbol, "reason": self._block_reason})
            self._candidate_sink(Candidate(decision.signal_ts, decision.symbol, False,
                                           "feed_stale", dict(decision.features)))
            return
        legs = self._allocate(decision)
        if decision.kind == "entry" and not legs:
            # No account could take a single leg (all halted/disabled, or every
            # notional cap rounded to zero). Mirror the entries-blocked branch: roll the
            # runtime back to FLAT so the symbol is not frozen in ENTRY_PENDING forever
            # (E-2 — diverged from the backtest, which always allocates its one account).
            rt = self.engine.runtime_for(decision.symbol)
            if rt is not None:
                rt.reset_entry()
            self.sink.emit("entry_skipped", bar.ts,
                           {"symbol": decision.symbol, "reason": "allocation"})
            self._candidate_sink(Candidate(decision.signal_ts, decision.symbol, False,
                                           "allocation", dict(decision.features)))
            return
        for aid, qty in legs:
            d = decision if (decision.kind != "entry" or qty == decision.qty_hint) \
                else replace(decision, qty_hint=qty)
            await self._run(aid, self._lc(aid).on_decision(d, bar), bar, source=decision)

    def _account_enabled(self, aid: str) -> bool:
        spec = self._specs.get(aid)
        return spec.enabled if spec is not None else True

    def _allocate(self, decision) -> list[tuple[str, int]]:
        active = [aid for aid in self._account_ids
                  if aid not in self._halted_accounts and self._account_enabled(aid)]
        if decision.kind != "entry":
            return [(aid, decision.qty_hint or 0) for aid in active]
        total = int(decision.qty_hint or 0)
        if not active or total <= 0:
            return []
        weights = [self._weights[aid] for aid in active]
        legs = _largest_remainder(total, weights)
        price = decision.limit_price
        out: list[tuple[str, int]] = []
        for aid, qty in zip(active, legs):
            spec = self._specs.get(aid)
            if spec is not None and spec.max_notional > 0 and price and price > 0:
                qty = min(qty, int(spec.max_notional // price))
            if qty > 0:
                out.append((aid, qty))
        return out

    async def _run(self, aid: str, actions: list[Action], bar: Bar, source=None) -> None:
        if aid in self._halted_accounts:
            return
        for a in actions:
            if isinstance(a, CancelAction):
                await self._broker_cancel(aid, a, bar)
            elif isinstance(a, SubmitAction):
                await self._submit(aid, a.intent, bar, source)

    async def _submit(self, aid: str, intent: OrderIntent, bar: Bar, source) -> None:
        is_entry = intent.side is Side.BUY
        ctx = self._trade_ctx(bar, intent.symbol, stop_price=intent.stop_price if is_entry else None)
        rc = self.risk.check(intent, ctx)
        if rc.verdict is RiskVerdict.REJECT or (is_entry and rc.qty <= 0):
            self.sink.emit("risk_reject", bar.ts, {"client_order_id": intent.client_order_id,
                                                   "symbol": intent.symbol, "reasons": list(rc.reasons)})
            if is_entry and source is not None:
                self._candidate_sink(Candidate(source.signal_ts, intent.symbol, False, "risk",
                                               dict(source.features)))
            await self._run(aid, self._lc(aid).on_order_rejected(intent.client_order_id, intent.symbol, bar), bar)
            return
        if is_entry and rc.qty != intent.qty:
            intent = replace(intent, qty=rc.qty)
        if is_entry and source is not None:
            self._candidate_sink(Candidate(source.signal_ts, intent.symbol, True, None,
                                           dict(source.features)))
        if await self._broker_submit(aid, intent, bar):
            self._order_reason[intent.client_order_id] = intent.reason
            self._order_account[intent.client_order_id] = aid
            self._order_ref[intent.client_order_id] = (
                bar.close, self.cost_model.slippage(bar.close, intent.side, intent.qty, bar.volume))
            self.sink.emit("order", bar.ts, order_payload(intent))

    # ------------------------------------------------------------------ context
    def _trade_ctx(self, bar: Bar, sym: str, *, stop_price: float | None) -> TradeContext:
        return TradeContext(
            now=bar.ts, mark_price=bar.close, session_window=self._window(bar),
            halted=sym in self._halted, spread_pct=None, luld_distance_pct=None,
            stop_price=stop_price, bar_volume=bar.volume)

    def _window(self, bar: Bar) -> str:
        return self._window_at(bar.ts)

    def _window_at(self, now: datetime) -> str:
        wins = self.engine.calendar.windows(now)
        for w in _WINDOW_PRIORITY:
            if w in wins:
                return w
        return "closed"
