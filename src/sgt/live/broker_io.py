"""Bounded, fault-handled broker I/O for the live executor (plan §13.4, fail-closed).

A mixin carrying every method that actually touches a broker adapter, so
:class:`sgt.live.executor.LiveExecutor` keeps only the per-bar control flow. Every call
is time-bounded (``asyncio.wait_for``); a timeout is surfaced as
``BrokerUnavailableError`` (retryable). ``submit`` retries on transient failures with the
SAME deterministic ``client_order_id`` (idempotent) up to a bound, then gives up and lets
the lifecycle re-protect later; an auth failure disarms the namespace and stops new
entries; a broker rejection routes to ``OrderLifecycle.on_order_rejected`` and is
journaled. A fill poller feeds real adapters' ``fills()`` into the shared queue.

The mixin reads attributes that ``LiveExecutor.__init__`` sets (adapters, risk, alerts,
sink, timeouts, the queue, lifecycles) and calls back into ``_run``/``_lc`` there.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from sgt.core.interfaces import (
    BrokerAuthError,
    BrokerError,
    BrokerRejectedError,
    BrokerUnavailableError,
    TradeContext,
)
from sgt.core.types import Bar, OrderIntent, RiskVerdict
from sgt.engine.orders import CancelAction

log = logging.getLogger(__name__)


def order_payload(intent: OrderIntent) -> dict:
    return {"client_order_id": intent.client_order_id, "symbol": intent.symbol,
            "side": intent.side.value, "qty": intent.qty, "order_type": intent.order_type.value,
            "limit_price": intent.limit_price, "stop_price": intent.stop_price, "reason": intent.reason}


class BrokerIOMixin:
    async def start(self) -> None:
        """Start background fill pollers for non-bar-driven (real) adapters."""
        self._running = True
        for aid in self._account_ids:
            if not self._bar_driven(aid):
                self._pollers.append(asyncio.create_task(self._poll_fills(aid)))

    async def aclose(self) -> None:
        self._running = False
        for task in self._pollers:
            task.cancel()
        for task in self._pollers:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._pollers.clear()

    async def submit_seed_intents(self, aid: str, intents, *, now: datetime,
                                  mark_price: float) -> None:
        """Submit the protective order(s) returned by ``OrderLifecycle.seed_position`` for
        a position reconstructed at startup. Risk-checked (a SELL exit/stop is approved
        against the seeded holding); a reject or broker failure is alerted, never hidden —
        an unprotected restarted position is an operator-grade event."""
        window = self._window_at(now)
        for intent in intents:
            ctx = TradeContext(now=now, mark_price=mark_price, session_window=window,
                               halted=intent.symbol in self._halted, spread_pct=None,
                               luld_distance_pct=None, stop_price=intent.stop_price, bar_volume=None)
            rc = self.risk.check(intent, ctx)
            if rc.verdict is RiskVerdict.REJECT:
                self._alerts.alert(f"SEED PROTECTION REJECTED {aid} {intent.symbol}",
                                   ",".join(rc.reasons))
                continue
            try:
                await self._bounded(lambda i=intent: self.adapters[aid].submit(i), "seed_submit")
            except BrokerError as exc:
                self._alerts.alert(f"SEED PROTECTION FAILED {aid} {intent.symbol}",
                                   f"{type(exc).__name__}: {exc}")
                continue
            self._order_reason[intent.client_order_id] = intent.reason
            self._order_account[intent.client_order_id] = aid
            self._order_ref[intent.client_order_id] = (mark_price, 0.0)
            self.sink.emit("order", now, order_payload(intent))

    async def _bounded(self, factory, op: str):
        try:
            return await asyncio.wait_for(factory(), timeout=self._op_timeout)
        except asyncio.TimeoutError as exc:
            raise BrokerUnavailableError(f"{op} timed out after {self._op_timeout}s") from exc

    async def _poll_fills(self, aid: str) -> None:
        ad = self.adapters[aid]
        while self._running:
            try:
                async for fill in ad.fills():
                    await self._queue.put((aid, fill))
            except BrokerAuthError as exc:
                self._on_auth_error(aid, exc)
            except BrokerUnavailableError as exc:
                self._alerts.alert(f"FILL POLL UNAVAILABLE {aid}", str(exc))
            except Exception as exc:  # bounded: a poller must not die silently
                log.warning("fill poller %s error: %s", aid, type(exc).__name__)
            await self._sleep(self._op_timeout)

    async def _broker_submit(self, aid: str, intent: OrderIntent, bar: Bar) -> bool:
        ad = self.adapters[aid]
        attempt = 0
        while True:
            try:
                await self._bounded(lambda: ad.submit(intent), "submit")
                return True
            except BrokerAuthError as exc:
                self._on_auth_error(aid, exc)
                await self._run(aid, self._lc(aid).on_order_rejected(
                    intent.client_order_id, intent.symbol, bar), bar)
                return False
            except BrokerRejectedError as exc:
                self.sink.emit("order_rejected", bar.ts, {
                    "client_order_id": intent.client_order_id, "symbol": intent.symbol,
                    "reason": str(exc)[:200]})
                await self._run(aid, self._lc(aid).on_order_rejected(
                    intent.client_order_id, intent.symbol, bar), bar)
                return False
            except BrokerUnavailableError as exc:
                attempt += 1
                if attempt > self._submit_retries:
                    self._alerts.alert(f"SUBMIT FAILED {aid} {intent.symbol}", str(exc))
                    await self._run(aid, self._lc(aid).on_order_rejected(
                        intent.client_order_id, intent.symbol, bar), bar)
                    return False
                await self._sleep(self._backoff_base * (2 ** (attempt - 1)))

    async def _broker_cancel(self, aid: str, action: CancelAction, bar: Bar) -> None:
        ad = self.adapters[aid]
        try:
            await self._bounded(lambda: ad.cancel(action.client_order_id), "cancel")
        except BrokerAuthError as exc:
            self._on_auth_error(aid, exc)
        except BrokerUnavailableError as exc:
            # A protective-stop cancel that fails is safe: the stop stays live at the broker.
            self._alerts.alert(f"CANCEL FAILED {aid} {action.symbol}", str(exc))
        self.sink.emit("cancel", bar.ts, {"client_order_id": action.client_order_id,
                                          "symbol": action.symbol, "reason": action.reason})

    def _on_auth_error(self, aid: str, exc: Exception) -> None:
        if aid not in self._auth_failed:
            self._auth_failed.add(aid)
            self._alerts.alert(f"BROKER AUTH FAILURE {aid}",
                               f"disarming {self.mode.value}: {type(exc).__name__}")
        self.risk.disarm(actor="system:auth", reason=f"broker_auth_error:{aid}")
