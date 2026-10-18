"""LiveRunner: orchestrates a SHADOW / PAPER / LIVE run end to end (plan §3.1, §11, §13).

Startup is strictly ordered and fail-closed:
  1. refuse the mode if the feed is not good enough (LIVE requires a real-time
     consolidated feed; PAPER refuses a delayed feed; SHADOW accepts delayed/IEX);
  2. the strategy is already loaded + validated in the constructor;
  3. build a fresh RiskManager (forced DISARMED on start; namespace from the mode) and
     register the strategy at the ladder floor (operators promote via the control plane);
  4. reconcile every account against its broker BEFORE any bar — a mismatch halts that
     account (no orders), never trades on a divergent view;
  5. reconstruct open positions from broker state (the broker-side protective stop is the
     protection of record across a restart; unprotected positions are alerted);
  6. start the risk session with broker equity;
  7. consume ``source.stream(watchlist)``.

While running: a Scanner rebuilds the watchlist on a cadence (symbols with open positions
always stay subscribed); per-minute position reconciliation halts an account on drift; a
feed stale for longer than ``staleness_s`` during a tradable window blocks new entries
(and resumes automatically when fresh); the halt feed routes into the engine. Shutdown
(SIGINT/SIGTERM) stops new entries, leaves protective stops at the broker, flushes the
event log and closes adapters — it NEVER auto-flattens unless ``flatten_on_exit`` is set.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from datetime import date, datetime, timezone
from typing import Callable, Mapping, Sequence

from sgt.accounts.reconcile import Reconciler
from sgt.accounts.registry import AccountSpec
from sgt.brokers.alert_only import LogNotifier, Notifier
from sgt.brokers.costs import DEFAULT_COST_MODEL, CostModel
from sgt.core.interfaces import BrokerAdapter, EventSink, LiveBarSource
from sgt.core.types import Bar, HaltEvent, Mode
from sgt.engine.engine import StrategyEngine
from sgt.engine.eventlog import InMemoryEventSink
from sgt.intraday.calendar import SessionCalendar, default_calendar
from sgt.live.alerts import AlertManager
from sgt.live.decay_runtime import BaselineMissingError, DecayRuntime
from sgt.live.executor import LiveExecutor
from sgt.live.heartbeat import close_session, open_session, write_heartbeat
from sgt.risk.exposure import DictExposureView
from sgt.risk.limits import LadderStage, RiskConfig
from sgt.risk.manager import RiskManager
from sgt.scanner.rank import Scanner
from sgt.strategy.base import Candidate
from sgt.strategy.loader import config_hash as compute_config_hash, load_strategy

log = logging.getLogger(__name__)

_DEFAULT_STALENESS_S = 150.0
_HALT_POLL_S = 30.0
_MINUTE_S = 60.0
_HALT_BACKOFF_S = 1.0  # backoff after a feed-stream error before re-subscribing

# BaselineMissingError is re-exported via the import above (``from sgt.live.runner import ...``).


class AdapterModeMismatchError(ValueError):
    """An adapter's liveness does not match the run mode (fail closed, L-C defense)."""


class FeedNotTradeableError(RuntimeError):
    """The configured feed is not acceptable for the requested mode (fail closed)."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LiveRunner:
    def __init__(
        self,
        config_path: str,
        mode: Mode,
        accounts: Sequence[AccountSpec],
        *,
        source: LiveBarSource,
        adapters: Mapping[str, BrokerAdapter],
        risk_config: RiskConfig,
        cost_model: CostModel | None = None,
        notifier: Notifier | None = None,
        event_sink: EventSink | None = None,
        reference: object = None,
        scanner: Scanner | None = None,
        scan_candidates: Callable[[datetime], Mapping[str, Sequence[Bar]]] | None = None,
        scan_reference: Callable[[str, date], object] | None = None,
        scan_attention: Callable[[str, datetime], Mapping[str, float]] | None = None,
        alert_symbols: Callable[[], set[str]] | None = None,
        initial_watchlist: Sequence[str] = (),
        staleness_s: float = _DEFAULT_STALENESS_S,
        max_watchlist: int = 50,
        flatten_on_exit: bool = False,
        run_id: str | None = None,
        ledger_positions_provider: Callable[[str], Mapping[str, int]] | None = None,
        ledger_cash_provider: Callable[[str], float] | None = None,
        halts_provider: Callable[[], Sequence[HaltEvent]] | None = None,
        calendar: SessionCalendar | None = None,
        entry_timeout_bars: int = 3,
        baseline_bars: Mapping[str, Sequence[Bar]] | None = None,
        dd_budget: float | None = None,
        allow_unknown_float: bool = False,
        clock: Callable[[], datetime] = _utcnow,
        sleep: Callable[[float], "asyncio.Future"] = asyncio.sleep,
    ) -> None:
        if mode is Mode.BACKTEST:
            raise ValueError("LiveRunner does not run BACKTEST mode; use run_backtest")
        self.mode = mode
        self.source = source
        self.adapters = dict(adapters)
        self.accounts = list(accounts)
        self.cost_model = cost_model or DEFAULT_COST_MODEL
        self._clock = clock
        self._sleep = sleep
        self._cal = calendar or default_calendar()
        self.staleness_s = staleness_s
        self.max_watchlist = max_watchlist
        self.flatten_on_exit = flatten_on_exit
        self._scanner = scanner
        self._scan_candidates = scan_candidates
        self._scan_reference = scan_reference
        self._scan_attention = scan_attention
        self._alert_symbols = alert_symbols
        self._halts_provider = halts_provider

        self.config = load_strategy(config_path)
        self.config_hash = compute_config_hash(self.config)
        self.run_id = run_id or f"{mode.value}-{self.config.id}-{int(self._clock().timestamp())}"

        self._candidates: list[Candidate] = []
        self.sink = event_sink or InMemoryEventSink()
        self.alerts = AlertManager(notifier or LogNotifier())

        self.exposure = DictExposureView()
        self.risk = RiskManager(risk_config, self.exposure, mode=mode, fresh_start=True)
        # Register the strategy at the ladder floor; LIVE requires an operator promotion
        # (and an arm) via the control plane before any entry can be placed.
        self.risk.set_strategy_stage(self.config.id, LadderStage.SHADOW, actor="runner")

        if allow_unknown_float and mode is Mode.LIVE:
            # F-8: real capital never trades a float-capped universe blind.
            raise ValueError("allow_unknown_float is not permitted in LIVE mode")
        # Decay + trade-journal collaborator (plan §11.4); built before the executor.
        self._decay_rt = DecayRuntime(
            config=self.config, config_hash=self.config_hash, mode=mode, run_id=self.run_id,
            risk=self.risk, event_sink=self.sink, alerts=self.alerts, risk_config=risk_config,
            clock=self._clock, baseline_bars=baseline_bars, dd_budget=dd_budget,
            reference=reference, allow_unknown_float=allow_unknown_float)
        self.engine = StrategyEngine(self.config, calendar=self._cal, reference=reference,
                                     candidate_sink=self._candidate_sink,
                                     allow_unknown_float=allow_unknown_float)
        self.executor = LiveExecutor(
            self.config, self.engine, risk=self.risk, adapters=self.adapters,
            exposure=self.exposure, cost_model=self.cost_model, event_sink=self.sink,
            candidate_sink=self._candidate_sink, alerts=self.alerts, run_id=self.run_id,
            config_hash=self.config_hash, mode=mode, accounts=self.accounts,
            entry_timeout_bars=entry_timeout_bars, on_closed_trade=self._on_closed_trade, sleep=sleep)

        self._reconciler = Reconciler(
            ledger_positions_provider or self._default_ledger_positions,
            on_halt=self._on_reconcile_halt,
            ledger_cash_provider=ledger_cash_provider)

        self._watchlist: list[str] = [s.upper() for s in initial_watchlist]
        self._base_watchlist: tuple[str, ...] = tuple(self._watchlist)
        self._last_bar_ts: datetime | None = None
        self._last_bar_by_symbol: dict[str, Bar] = {}
        self._last_arrival: datetime | None = None
        self._stale_handled = False  # one executor.on_feed_stale call per stall (L-E)
        self._seen_halts: set[tuple[str, datetime, str]] = set()
        self._stop = asyncio.Event()
        self._resub = asyncio.Event()
        self._started = False

    # ------------------------------------------------------------------ candidates
    def _candidate_sink(self, c: Candidate) -> None:
        self._candidates.append(c)
        self.sink.emit("candidate", c.ts, {"symbol": c.symbol, "taken": c.taken,
                                           "skip_reason": c.skip_reason or ""})

    def _default_ledger_positions(self, account_id: str) -> Mapping[str, int]:
        from sgt.accounts.ledger import ledger_positions
        from sgt.db.session import get_session
        with get_session() as s:
            return ledger_positions(s, account_id)

    # Decay logic lives in the DecayRuntime collaborator (§11.4); these thin accessors keep
    # the pre-split call surface (the executor's closed-trade hook, the tests) unchanged.
    @property
    def _supervisor(self):
        return self._decay_rt.supervisor

    @property
    def _decay(self):
        return self._decay_rt.decay

    def _on_closed_trade(self, row: dict) -> None:
        self._decay_rt.on_closed_trade(row)

    def _feed_decay(self, row: dict) -> None:
        self._decay_rt.feed(row)

    # ------------------------------------------------------------------ startup
    async def startup(self) -> None:
        self._check_feed()
        self._check_adapters()
        self._decay_rt.setup()
        open_session(self.run_id, self.mode.value, self.config_hash, self._clock())
        await self._reconcile_all(positions_only=False)
        await self._reconstruct_positions()
        now = self._clock()
        session_date = self._cal.session_of(now) or now.date()
        await self.executor.ensure_session_started(session_date)
        await self.executor.start()
        self._started = True
        self.alerts.alert(f"RUN STARTED {self.run_id}",
                          f"mode={self.mode.value} disarmed; accounts={len(self.adapters)}")

    def _check_feed(self) -> None:
        src = self.source
        if self.mode is Mode.LIVE and not src.is_live_tradeable:
            raise FeedNotTradeableError(
                f"LIVE requires a real-time consolidated feed; got delay={src.delay_minutes}min "
                f"consolidated={src.consolidated}")
        if self.mode is Mode.PAPER and src.delay_minutes > 0:
            raise FeedNotTradeableError(
                f"PAPER refuses a delayed feed (delay={src.delay_minutes}min); use SHADOW")

    def _check_adapters(self) -> None:
        """Defense in depth (L-C): an adapter whose liveness disagrees with the mode must
        never reach a broker. For PAPER/LIVE ``adapter.is_paper`` must equal (mode is PAPER);
        SHADOW accepts only paper/sim adapters (it places no real orders)."""
        for aid, ad in self.adapters.items():
            is_paper = bool(getattr(ad, "is_paper", False))
            if self.mode in (Mode.PAPER, Mode.LIVE):
                if is_paper != (self.mode is Mode.PAPER):
                    raise AdapterModeMismatchError(
                        f"account {aid!r}: {self.mode.value} mode requires a "
                        f"{'paper' if self.mode is Mode.PAPER else 'live'} adapter, "
                        f"got {type(ad).__name__} (is_paper={is_paper})")
            elif not is_paper:  # SHADOW
                raise AdapterModeMismatchError(
                    f"account {aid!r}: SHADOW accepts only SimBroker/paper adapters, "
                    f"got live-capable {type(ad).__name__} (is_paper=False)")

    async def _reconcile_all(self, *, positions_only: bool) -> None:
        for acct in self.accounts:
            ad = self.adapters.get(acct.account_id)
            if ad is None or acct.account_id in self.executor._halted_accounts:
                continue
            try:
                await self._reconciler.run(ad)
            except Exception as exc:  # a reconcile failure is fail-closed: halt the account
                self._on_reconcile_halt(acct.account_id, f"reconcile_error:{type(exc).__name__}")

    def _on_reconcile_halt(self, account_id: str, reason: str) -> None:
        self.executor.halt_account(account_id, reason)

    def _stop_pct(self) -> float:
        for c in self.config.exits.any_of:
            if getattr(c, "kind", "") == "stop_loss_pct":
                return float(c.pct)
        return 0.0

    async def _reconstruct_positions(self) -> None:
        """Seed each open broker position into the engine + lifecycle (so it is exited,
        never re-entered), adopt any existing broker protective stop, and otherwise place
        one immediately (or exit if already below the stop)."""
        now = self._clock()
        session_date = self._cal.session_of(now) or now.date()
        stop_pct = self._stop_pct()
        for acct in self.accounts:
            aid = acct.account_id
            ad = self.adapters.get(aid)
            if ad is None or aid in self.executor._halted_accounts:
                continue
            state = await ad.account_state()
            working = await ad.open_orders()
            for pos in state.positions:
                if pos.qty <= 0:
                    continue
                stop_price = pos.avg_price * (1.0 - stop_pct / 100.0) if stop_pct > 0 else 0.0
                self.engine.seed_position(pos.symbol, pos.qty, pos.avg_price, now, session_date)
                self.executor.seed_position(aid, pos.symbol, pos.qty, pos.avg_price)
                sym_working = [a for a in working if a.symbol == pos.symbol]
                intents = self.executor._lc(aid).seed_position(
                    pos.symbol, pos.qty, pos.avg_price, stop_price=stop_price,
                    working_orders=sym_working, now=now, mark_price=pos.avg_price)
                await self.executor.submit_seed_intents(aid, intents, now=now, mark_price=pos.avg_price)
                self.alerts.alert(
                    f"STARTUP POSITION {aid} {pos.symbol}",
                    f"qty={pos.qty} avg={pos.avg_price} stop={stop_price:.4f} "
                    f"({'placed' if intents else 'adopted'})")

    # ------------------------------------------------------------------ bar handling
    async def _on_bar(self, bar: Bar) -> None:
        self._last_arrival = self._clock()
        self._last_bar_ts = bar.ts
        self._last_bar_by_symbol[bar.symbol] = bar
        self._stale_handled = False  # feed is fresh again: re-arm one-shot stale handling (L-E)
        if self.executor._entries_blocked:
            self.executor.unblock_entries()
        await self.executor.process_bar(bar)

    def _is_tradable_window(self, now: datetime) -> bool:
        wins = self._cal.windows(now)
        allowed = self.risk._cfg.limits.allowed_windows
        return bool(wins & allowed)

    async def _check_staleness(self, now: datetime) -> bool:
        """Block new entries if the feed has gone stale during a tradable window.
        Returns True if entries are currently blocked for staleness."""
        if self._last_arrival is None or not self._is_tradable_window(now):
            return self.executor._entries_blocked
        elapsed = (now - self._last_arrival).total_seconds()
        if elapsed > self.staleness_s:
            if not self.executor._entries_blocked:
                self.executor.block_entries("feed_stale")
                self.alerts.alert("FEED STALE",
                                  f"no bar for {elapsed:.0f}s > {self.staleness_s:.0f}s")
            # Open positions during a stall are unprotected if a broker stop went missing:
            # hand off to the executor once per stall to re-place stops + alert critically (L-E).
            if not self._stale_handled and self.executor.open_position_count() > 0:
                self._stale_handled = True
                await self.executor.on_feed_stale(now)
            return True
        return self.executor._entries_blocked

    async def _write_heartbeat(self, now: datetime) -> None:
        write_heartbeat(self.run_id, ts=now, last_bar_ts=self._last_bar_ts,
                        subscribed=len(self._watchlist),
                        open_positions=self.executor.open_position_count())

    async def _poll_halts(self) -> None:
        if self._halts_provider is None:
            return
        for ev in self._halts_provider():
            key = (ev.symbol, ev.ts, ev.state.value)
            if key in self._seen_halts:
                continue
            self._seen_halts.add(key)
            self.executor.process_halt(ev)

    def _held_symbols(self) -> set[str]:
        return self.executor.held_symbols()

    async def _rescan_watchlist(self, now: datetime) -> list[str]:
        held = self._held_symbols()
        if self._scanner is None or self._scan_candidates is None or self._scan_reference is None:
            # No scanner: held names first, then the operator watchlist, then symbols alerted
            # by Telegram/TradingView (alerts never displace held or configured names).
            alerts = sorted(s.upper() for s in (self._alert_symbols() if self._alert_symbols else ()))
            new = list(dict.fromkeys(sorted(held) + list(self._base_watchlist) + alerts))[: self.max_watchlist]
        else:
            items = self._scanner.scan(
                now, self._scan_candidates(now), reference=self._scan_reference,
                attention=self._scan_attention,
                alert_symbols=self._alert_symbols() if self._alert_symbols else frozenset())
            ranked = [w.symbol for w in items]
            new = list(dict.fromkeys(list(held) + ranked))[: self.max_watchlist]
        if set(new) != set(self._watchlist):
            self._watchlist = new
            self._resub.set()
        return self._watchlist

    # ------------------------------------------------------------------ run / shutdown
    async def run(self) -> None:
        await self.startup()
        await self.serve()

    async def serve(self) -> None:
        """Run the consume/periodic/halt loops until a stop signal, then shut down.
        Call after :meth:`startup` (``run`` does both)."""
        self._install_signal_handlers()
        tasks = [
            asyncio.create_task(self._consume_loop()),
            asyncio.create_task(self._periodic_loop()),
            asyncio.create_task(self._halt_loop()),
        ]
        await self._stop.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.shutdown(self.flatten_on_exit)

    async def _consume_loop(self) -> None:
        while not self._stop.is_set():
            if not self._watchlist:
                await self._rescan_watchlist(self._clock())
            self._resub.clear()
            stream = self.source.stream(self._watchlist)
            try:
                async for bar in stream:
                    await self._on_bar(bar)
                    if self._stop.is_set() or self._resub.is_set():
                        break
            except Exception as exc:  # a stream error must not kill the run silently
                self.alerts.alert("FEED STREAM ERROR", type(exc).__name__)
                await self._sleep(_HALT_BACKOFF_S)
            finally:
                aclose = getattr(stream, "aclose", None)
                if aclose is not None:
                    await aclose()

    async def _periodic_loop(self) -> None:
        last_scan = self._clock()
        while not self._stop.is_set():
            await self._sleep(_MINUTE_S)
            now = self._clock()
            await self._reconcile_all(positions_only=True)
            await self._check_staleness(now)
            await self._write_heartbeat(now)
            if (now - last_scan).total_seconds() >= 5 * _MINUTE_S:
                await self._rescan_watchlist(now)
                last_scan = now

    async def _halt_loop(self) -> None:
        while not self._stop.is_set():
            await self._poll_halts()
            await self._sleep(_HALT_POLL_S)

    def _install_signal_handlers(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self.request_stop)
        except (NotImplementedError, RuntimeError):
            # Not supported (non-main thread / Windows): shutdown relies on request_stop().
            log.info("signal handlers unavailable; use request_stop() for graceful shutdown")

    def request_stop(self) -> None:
        self._stop.set()

    async def shutdown(self, flatten: bool) -> None:
        """Graceful shutdown: stop new entries, keep broker-side protective stops, flush
        the event log and close adapters. Never auto-flatten unless ``flatten`` is set."""
        self.executor.block_entries("shutdown")
        if flatten and self._last_bar_by_symbol:
            self.executor.close_open_positions(dict(self._last_bar_by_symbol))
        await self.executor.aclose()
        self.sink.flush()
        for ad in self.adapters.values():
            try:
                await ad.close()
            except Exception as exc:
                log.warning("adapter close failed: %s", type(exc).__name__)
        try:
            await self.source.close()
        except Exception as exc:
            log.warning("source close failed: %s", type(exc).__name__)
        close_session(self.run_id, "flattened" if flatten else "stopped", self._clock())
        self.alerts.alert(f"RUN STOPPED {self.run_id}", f"flatten={flatten}")
