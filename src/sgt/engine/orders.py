"""Broker-agnostic order lifecycle (plan §7.3, §8; F-5 fixes).

``OrderLifecycle`` owns everything about turning a strategy's :class:`Decision`s
into live orders and keeping an open position *always* protected:

- translates entry/exit/cancel decisions into orders,
- places a protective STOP on every entry fill (where the broker supports stops),
- cancels that stop only while a discretionary exit order is actually working, and
  re-places it the moment that exit terminates unfilled,
- retries an exit that reaches a terminal state (expired / canceled / rejected)
  without fully filling — a fired exit is a commitment, not re-evaluated,
- cancels an entry that is not filled within ``entry_timeout_bars``,
- force-exits a position that crosses a session boundary still open
  (``session_end_forced`` for flatten strategies, else ``carried_overnight``),
- reports the open-position invariant:
  ``open position ⇒ working stop OR working exit OR segment-without-stop-support``.

It is PURE and broker-agnostic: it never imports a broker or calls a broker API.
It CONSUMES events and EMITS actions; the adapter (``BacktestExecutor`` here, a
future async ``LiveExecutor`` next) executes the actions and feeds results back.

Public API
----------
``OrderLifecycle(config, *, account_id, strategy_id, config_hash, caps, runtime_for,
entry_timeout_bars=3)`` where ``caps`` is :class:`BrokerCaps` and ``runtime_for`` is
``Callable[[str], StrategyRuntime | None]``.

Event methods (each returns ``list[Action]`` unless noted):
- ``on_session_roll(symbol, bar)`` — a bar crossed into a new session for ``symbol``.
- ``on_fill(fill, bar)`` — a broker fill (BUY entry / SELL exit or stop).
- ``on_order_terminal(ack, bar)`` — an order reached a terminal ``OrderAck.status``
  (expired/canceled/rejected). The adapter supplies this explicitly; the lifecycle
  never polls a broker.
- ``on_bar_tick(bar)`` — advance entry-timeout accounting for ``bar.symbol``.
- ``on_decision(decision, bar)`` — translate one runtime decision.
- ``check_invariant(bar, *, halted)`` — returns violation message(s); ``[]`` if sound.
- ``open_order_ids()`` — ids the adapter should watch for terminal transitions.

Actions are :class:`SubmitAction` / :class:`CancelAction` — the adapter maps these
to its broker (sync ``submit_sync``/``cancel_sync`` or async ``submit``/``cancel``).
"""
from __future__ import annotations

from datetime import datetime

from sgt.core.ids import client_order_id
from sgt.core.types import Bar, Fill, OrderAck, OrderIntent, Segment, Side
from sgt.engine.orders_types import (
    Action,
    BrokerCaps,
    CancelAction,
    RuntimeFor,
    SubmitAction,
    _is_protective,
    _Order,
    _Sym,
    entry_intent,
    exit_intent,
    seed_exit_intent,
    seed_stop_intent,
    stop_intent,
)
from sgt.strategy.base import Decision
from sgt.strategy.schema import StrategyConfig

# The value types, internal order/symbol records and intent builders moved to
# sgt.engine.orders_types (2026-10-13 split of orders.py below the 450-line limit).
# Re-export the public names so existing ``from sgt.engine.orders import X`` imports
# (broker_io, both executors, tests) keep resolving here unchanged.
__all__ = ["OrderLifecycle", "BrokerCaps", "SubmitAction", "CancelAction", "Action",
           "RuntimeFor"]


class OrderLifecycle:
    def __init__(
        self,
        config: StrategyConfig,
        *,
        account_id: str,
        strategy_id: str,
        config_hash: str,
        caps: BrokerCaps,
        runtime_for: RuntimeFor,
        entry_timeout_bars: int = 3,
        protective_slippage_pct: float = 0.03,
    ) -> None:
        if not 0.0 < protective_slippage_pct < 1.0:
            raise ValueError("protective_slippage_pct must be in (0, 1)")
        self.config = config
        self.account_id = account_id
        self.strategy_id = strategy_id
        self.config_hash = config_hash
        self.caps = caps
        self._runtime_for = runtime_for
        self._timeout = max(1, entry_timeout_bars)
        self._protective_slip = float(protective_slippage_pct)
        self._has_flatten = any(c.kind == "session_end" for c in config.exits.any_of)
        self._ext = caps.supports_extended_hours and config.extended_hours
        self._state: dict[str, _Sym] = {}
        self._orders: dict[str, _Order] = {}
        # Open-order index (2026-10-17 perf fix): open_order_ids() previously scanned
        # every order ever placed on every bar (quadratic). Insertion-ordered so the
        # iteration order -- and therefore the event log -- is unchanged.
        self._open: dict[str, _Order] = {}
        self._leg = 0

    # ------------------------------------------------------------------ helpers
    def _sym(self, symbol: str) -> _Sym:
        st = self._state.get(symbol)
        if st is None:
            st = _Sym()
            self._state[symbol] = st
        return st

    def _cid(self, symbol: str, signal_ts: datetime, reason: str) -> str:
        self._leg += 1
        return client_order_id(strategy_id=self.config_hash, account_id=self.account_id,
                               symbol=symbol, signal_ts=signal_ts, reason=reason, leg=self._leg)

    def _cancel_stop(self, st: _Sym, actions: list[Action], symbol: str) -> None:
        if st.stop is not None and st.stop.working:
            st.stop.terminal = True
            actions.append(CancelAction(st.stop.cid, symbol, "superseded_by_exit"))
        st.stop = None

    def _place_stop(self, st: _Sym, symbol: str, bar: Bar) -> list[Action]:
        # Size to the CURRENTLY held quantity: a stop larger than the position is a
        # short sale and the risk gate rejects it (which must never loop into a retry);
        # a stop SMALLER than the position (left over after a later partial entry fill,
        # or a second coexisting entry) leaves shares unprotected. 2026-10-13 (E-1):
        # if a working stop no longer covers the full position, cancel it and replace
        # with one sized to the whole position — never two working stops, never > held.
        if not self.caps.supports_broker_side_stops or st.pos_qty <= 0 or st.stop_price <= 0:
            return []
        actions: list[Action] = []
        if st.stop is not None and st.stop.working:
            if st.stop.qty == st.pos_qty:
                return []
            st.stop.terminal = True
            actions.append(CancelAction(st.stop.cid, symbol, "resize_stop"))
            st.stop = None
        cid = self._cid(symbol, bar.ts, "protective_stop")
        order = _Order(cid, "stop", symbol, st.pos_qty)
        self._orders[cid] = order
        self._open[cid] = order
        st.stop = order
        actions.append(SubmitAction(stop_intent(
            symbol, order.qty, bar, st.stop_price, cid,
            account_id=self.account_id, strategy_id=self.strategy_id)))
        return actions

    def _needs_marketable(self, bar: Bar) -> bool:
        """True in any segment where a broker-side STOP is not live, so a protective
        exit must be marketable to fill rather than rest far from a gapping price."""
        return not self.caps.supports_broker_side_stops or bar.segment is not Segment.RTH

    def _protective_limit(self, bar: Bar) -> float:
        # Marketable SELL limit below the close; clamped above zero (a LIMIT must be > 0).
        return round(max(bar.close * (1.0 - self._protective_slip), 1.0e-6), 6)

    def _submit_exit(self, st: _Sym, symbol: str, bar: Bar, reason: str,
                     limit: float | None = None) -> list[Action]:
        actions: list[Action] = []
        self._cancel_stop(st, actions, symbol)
        if _is_protective(reason) and self._needs_marketable(bar):
            limit = self._protective_limit(bar)  # override any resting-limit offset
        cid = self._cid(symbol, bar.ts, reason)
        order = _Order(cid, "exit", symbol, st.pos_qty, reason=reason)
        self._orders[cid] = order
        self._open[cid] = order
        st.exit = order
        intent = exit_intent(symbol, st.pos_qty, bar, reason, cid,
                             account_id=self.account_id, strategy_id=self.strategy_id,
                             ext=self._ext, exit_offset_bps=self.config.sizing.exit_offset_bps,
                             limit=limit)
        order.limit_price = intent.limit_price or 0.0
        actions.append(SubmitAction(intent))
        rt = self._runtime_for(symbol)
        if rt is not None:
            rt.enter_exit_pending()
        return actions

    # ------------------------------------------------------------------ restart seeding
    def seed_position(
        self,
        symbol: str,
        qty: int,
        avg_price: float,
        *,
        stop_price: float,
        working_orders: list[OrderAck],
        now: datetime,
        mark_price: float,
    ) -> list[OrderIntent]:
        """Reconstruct a held position from broker state after a restart and return the
        protective order(s) the caller must submit so the open-position invariant holds.

        A working protective SELL already at the broker (surviving the process restart) is
        ADOPTED as the stop (no new order). Otherwise, if the broker supports stops, a
        STOP is placed at ``stop_price``; if the current mark is already at/below the stop,
        a marketable-limit EXIT is returned instead. With no broker-side stop support the
        runtime bar-close stop is the fallback (the invariant permits it)."""
        qty = int(qty)
        if qty <= 0:
            return []
        st = self._sym(symbol)
        st.pos_qty = qty
        st.entry_qty = qty
        st.stop_price = float(stop_price or 0.0)
        st.entry_age = 0
        for ack in working_orders:
            if (ack.symbol == symbol and ack.side is Side.SELL
                    and not ack.status.terminal and (ack.remaining_qty or 0) > 0):
                order = _Order(ack.client_order_id, "stop", symbol, int(ack.remaining_qty or qty))
                self._orders[ack.client_order_id] = order
                self._open[ack.client_order_id] = order
                st.stop = order
                return []
        if not self.caps.supports_broker_side_stops or st.stop_price <= 0:
            return []
        if mark_price <= st.stop_price:
            cid = self._cid(symbol, now, "seed_exit")
            order = _Order(cid, "exit", symbol, qty, reason="exit:seed_stop")
            self._orders[cid] = order
            self._open[cid] = order
            st.exit = order
            return [seed_exit_intent(symbol, qty, now, mark_price, cid,
                                     account_id=self.account_id, strategy_id=self.strategy_id,
                                     ext=self._ext,
                                     exit_offset_bps=self.config.sizing.exit_offset_bps)]
        cid = self._cid(symbol, now, "seed_stop")
        order = _Order(cid, "stop", symbol, qty)
        self._orders[cid] = order
        self._open[cid] = order
        st.stop = order
        return [seed_stop_intent(symbol, qty, now, st.stop_price, cid,
                                 account_id=self.account_id, strategy_id=self.strategy_id)]

    # ------------------------------------------------------------------ events
    def on_decision(self, d: Decision, bar: Bar) -> list[Action]:
        st = self._sym(d.symbol)
        if d.kind == "entry":
            # Never run two entry orders for one (account, symbol) concurrently: a second
            # entry while the first is working/partially filled would build a position
            # larger than the sizing decision and strand the undersized stop (E-1). Reject
            # it and roll the runtime back to FLAT so the symbol is not frozen.
            if st.entry is not None and st.entry.working:
                rt = self._runtime_for(d.symbol)
                if rt is not None:
                    rt.reset_entry()
                return []
            st.entry_qty = int(d.qty_hint or 0)
            st.stop_price = float(d.stop_price or 0.0)
            st.entry_age = 0
            cid = self._cid(d.symbol, d.signal_ts, d.reason)
            order = _Order(cid, "entry", d.symbol, st.entry_qty)
            self._orders[cid] = order
            self._open[cid] = order
            st.entry = order
            return [SubmitAction(entry_intent(d, bar, cid, account_id=self.account_id,
                                              strategy_id=self.strategy_id, ext=self._ext))]
        if d.kind == "exit":
            if st.pos_qty <= 0:
                return []
            return self._submit_exit(st, d.symbol, bar, d.reason, d.limit_price)
        if d.kind == "cancel" and st.entry is not None and st.entry.working:
            st.entry.terminal = True
            cid = st.entry.cid
            st.entry = None
            return [CancelAction(cid, d.symbol, "entry_cancel")]
        return []

    def on_fill(self, fill: Fill, bar: Bar) -> list[Action]:
        o = self._orders.get(fill.client_order_id)
        if o is not None:
            o.filled += fill.qty
        st = self._sym(fill.symbol)
        if fill.side is Side.BUY:
            st.pos_qty += fill.qty
            if st.entry is not None:
                st.entry_age = 0  # a filling entry no longer times out
            return self._place_stop(st, fill.symbol, bar)
        # SELL (discretionary exit or protective stop)
        st.pos_qty = max(0, st.pos_qty - fill.qty)
        if st.pos_qty == 0:
            actions: list[Action] = []
            if st.stop is not None and st.stop.working:
                st.stop.terminal = True
                actions.append(CancelAction(st.stop.cid, fill.symbol, "position_closed"))
            st.stop = None
            st.exit = None
            st.entry = None
            return actions
        return []

    def on_order_terminal(self, ack: OrderAck, bar: Bar) -> list[Action]:
        o = self._orders.get(ack.client_order_id)
        if o is None or o.terminal:
            return []
        o.terminal = True
        st = self._sym(o.symbol)
        if o.kind == "entry":
            if o is st.entry:
                st.entry = None
            if st.pos_qty <= 0:
                rt = self._runtime_for(o.symbol)
                if rt is not None:
                    rt.reset_entry()
            return []
        if o.kind == "stop":
            if o is st.stop:
                st.stop = None
            # A protective stop that terminated with the position still open must be
            # restored (unless a discretionary exit is working).
            if st.pos_qty > 0 and not (st.exit is not None and st.exit.working):
                return self._place_stop(st, o.symbol, bar)
            return []
        # exit terminated unfilled while still holding -> retry the same commitment
        if o is st.exit:
            st.exit = None
        if st.pos_qty > 0:
            return self._submit_exit(st, o.symbol, bar, o.reason)
        return []

    def on_session_roll(self, symbol: str, bar: Bar) -> list[Action]:
        st = self._sym(symbol)
        if st.pos_qty <= 0:
            return []
        # Clear any stale (now-expired) exit from the prior session.
        if st.exit is not None:
            st.exit.terminal = True
            st.exit = None
        reason = "session_end_forced" if self._has_flatten else "carried_overnight"
        return self._submit_exit(st, symbol, bar, reason)

    def on_bar_tick(self, bar: Bar) -> list[Action]:
        actions = self._reprice_protective_exit(bar)
        st = self._state.get(bar.symbol)
        if st is None or st.entry is None or not st.entry.working or st.entry.filled > 0:
            return actions
        st.entry_age += 1
        if st.entry_age > self._timeout:
            cid = st.entry.cid
            st.entry.terminal = True
            st.entry = None
            rt = self._runtime_for(bar.symbol)
            if rt is not None:
                rt.reset_entry()
            actions.append(CancelAction(cid, bar.symbol, "entry_timeout"))
        return actions

    def _reprice_protective_exit(self, bar: Bar) -> list[Action]:
        """While a marketable protective exit rests unfilled in a no-broker-stop segment,
        re-price it to the latest bar's close each eligible bar (cancel/replace, same
        latch) so a falling price cannot leave the resting limit permanently above the
        market. No-op once back in a segment that supports broker stops, or if the price
        is unchanged (avoids churn)."""
        st = self._state.get(bar.symbol)
        if st is None or st.pos_qty <= 0:
            return []
        ex = st.exit
        if ex is None or not ex.working or not _is_protective(ex.reason):
            return []
        if not self._needs_marketable(bar):
            return []
        new_limit = self._protective_limit(bar)
        if new_limit == ex.limit_price:
            return []
        ex.terminal = True
        st.exit = None
        actions: list[Action] = [CancelAction(ex.cid, bar.symbol, "reprice_protective")]
        return actions + self._submit_exit(st, bar.symbol, bar, ex.reason)

    def on_order_rejected(self, cid: str, symbol: str, bar: Bar) -> list[Action]:
        """The adapter's risk gate refused a proposed order. Drop the tracked order
        WITHOUT a synchronous retry (retrying the same rejected order the same bar
        would loop); protection is re-established on a later bar/fill as applicable."""
        o = self._orders.get(cid)
        if o is None or o.terminal:
            return []
        o.terminal = True
        st = self._sym(symbol)
        if o is st.entry:
            st.entry = None
            rt = self._runtime_for(symbol)
            if rt is not None and st.pos_qty <= 0:
                rt.reset_entry()
        elif o is st.stop:
            st.stop = None
        elif o is st.exit:
            st.exit = None
        return []

    def flatten(self, symbol: str, bar: Bar, reason: str = "risk_flatten") -> list[Action]:
        """Force a protective exit of the whole held position (risk-budget breach, plan
        §11.6). Latched: if an exit is already working this is a no-op, so repeated
        flatten calls across bars never stack duplicate orders."""
        st = self._state.get(symbol)
        if st is None or st.pos_qty <= 0:
            return []
        if st.exit is not None and st.exit.working:
            return []
        return self._submit_exit(st, symbol, bar, reason)

    def ensure_protective_stop(self, symbol: str, bar: Bar) -> list[Action]:
        """Re-place a missing broker-side protective stop for a held position where the
        segment supports one (stale-feed recovery, plan §11). No-op if a stop or exit is
        already working, or the segment/broker has no broker-side stop."""
        st = self._state.get(symbol)
        if st is None or st.pos_qty <= 0:
            return []
        if not self.caps.supports_broker_side_stops or bar.segment is not Segment.RTH:
            return []
        if (st.stop is not None and st.stop.working) or (st.exit is not None and st.exit.working):
            return []
        return self._place_stop(st, symbol, bar)

    # ------------------------------------------------------------------ queries
    def open_order_ids(self) -> list[str]:
        dead = [cid for cid, o in self._open.items() if not o.working]
        for cid in dead:
            del self._open[cid]
        return list(self._open)

    def position_qty(self, symbol: str) -> int:
        st = self._state.get(symbol)
        return st.pos_qty if st else 0

    def held_symbols(self) -> list[str]:
        return [sym for sym, st in self._state.items() if st.pos_qty > 0]

    def check_invariant(self, bar: Bar, *, halted: bool) -> list[str]:
        st = self._state.get(bar.symbol)
        if st is None or st.pos_qty <= 0:
            return []
        if halted or bar.segment is not Segment.RTH or not self.caps.supports_broker_side_stops:
            return []  # extended hours / halted / no-stop-broker: runtime bar-close stop is the fallback
        stop_working = st.stop is not None and st.stop.working
        exit_working = st.exit is not None and st.exit.working
        # A working stop that does not cover the whole position (undersized after a partial
        # entry fill) or exceeds it (an impossible short) is itself an invariant breach (E-1).
        if stop_working and st.stop.qty != st.pos_qty:
            rel = "below" if st.stop.qty < st.pos_qty else "above"
            return [f"working stop {bar.symbol} qty={st.stop.qty} {rel} position qty={st.pos_qty}"]
        if stop_working or exit_working:
            return []
        return [f"open position {bar.symbol} qty={st.pos_qty} in RTH with no working stop or exit"]
