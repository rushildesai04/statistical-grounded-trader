"""RiskManager: the fail-closed pre-trade gate and PnL/budget bookkeeper.

Implements the core ``RiskGate`` protocol (sgt.core.interfaces). Every entry is checked
against the capital-protection doctrine (IMPLEMENTATION_PLAN §1, §11.4-11.6) BEFORE it can
reach a broker adapter. Exits (a SELL reducing a long) bypass budget/limit/profit-lock/
window rules — blocking an exit would amplify losses — and are stopped only by a halt or an
over-sell. Any exception inside ``check`` yields REJECT ``internal_error:<type>`` and is
audited (the one documented fail-closed swallow, the audit-of-last-resort).

Loss guardrails use realized+unrealized PnL: open-position bleed is tracked via
``mark_to_market`` and surfaced by ``flatten_scopes`` and the entry checks (L-A), so a
position cannot lose past a limit before a fill books it. Unrealized is recomputed from
marks and never persisted; the realized ledger and locks persist as before.

Durable truth (switch, strategy status, fill ledger, audit) lives in a store bound to one
``namespace`` so modes never share state (F-1); hot state (positions, PnL, daily counters,
lockouts) is rebuilt by replaying the ledger, so budgets and cost basis survive a restart.
The switch is forced DISARMED on start; daily counters reset only via ``start_session``.
Daily loss limits + profit lock apply in ALL modes; learning/per-strategy budgets, their
auto-disarm/disable, and ladder gating apply in LIVE only. Fill->strategy attribution uses
the check-time map, falling back to ``strategy_resolver``.
"""
from __future__ import annotations

import logging

import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Mapping

from sgt.core.interfaces import TradeContext
from sgt.core.types import Fill, Mode, OrderIntent, RiskDecision, RiskVerdict, Side
from sgt.risk.exposure import ExposureView
from sgt.risk.limits import LadderStage, RiskConfig
from sgt.risk.pnl import UnrealizedBook, compute_breaches
from sgt.risk.settlement import SettlementTracker
from sgt.risk.state import RiskStore, RiskStoreLike

_log = logging.getLogger(__name__)

_MODE_NAMESPACE: dict[Mode, str] = {
    Mode.LIVE: "live",
    Mode.PAPER: "paper",
    Mode.SHADOW: "shadow",
}


def _cap(budget: float, unit: float) -> int:
    """Max whole shares affordable under ``budget`` at ``unit`` price (floor).

    A small epsilon absorbs float representation error so an order exactly at a limit
    is not spuriously reduced by one share."""
    if unit <= 0:
        return 0
    return int(math.floor((budget + 1e-9) / unit))


@dataclass(slots=True)
class _Pos:
    qty: int
    avg_cost: float


class RiskManager:
    def __init__(self, config: RiskConfig, exposure: ExposureView, *,
                 store: RiskStoreLike | None = None, settlement: SettlementTracker | None = None,
                 mode: Mode = Mode.LIVE, namespace: str | None = None, fresh_start: bool = True,
                 strategy_resolver: Callable[[str], str | None] | None = None) -> None:
        self._cfg = config
        self._exposure = exposure
        if store is None:
            store = RiskStore(self._namespace_for(mode, namespace))
        self._store: RiskStoreLike = store
        self._settlement = settlement
        self._mode = mode
        self._live = mode is Mode.LIVE
        self._strategy_resolver = strategy_resolver
        self._positions: dict[tuple[str, str], _Pos] = {}
        self._pos_strategy: dict[tuple[str, str], str] = {}
        self._unrealized = UnrealizedBook()
        self._last_mark_ts: datetime | None = None
        self._program_pnl = 0.0
        self._strategy_pnl: dict[str, float] = {}
        self._daily_global_pnl = 0.0
        self._daily_pnl_by_account: dict[str, float] = {}
        self._daily_entry_orders: set[str] = set()
        self._daily_locked_global = False
        self._daily_locked_accounts: set[str] = set()
        self._profit_locked = False
        self._profit_lock_threshold: float | None = None
        self._program_budget_tripped = False
        self._order_strategy: dict[str, str] = {}
        self._store.load(fresh_start=fresh_start)
        self._replay()

    @staticmethod
    def _namespace_for(mode: Mode, namespace: str | None) -> str:
        if namespace:
            return namespace
        ns = _MODE_NAMESPACE.get(mode)
        if ns is None:
            raise ValueError(
                f"mode {mode.value!r} requires an explicit namespace (BACKTEST runs must "
                "isolate their risk state); pass namespace=... or a store=...")
        return ns

    # ------------------------------------------------------------------ RiskGate
    def check(self, intent: OrderIntent, ctx: TradeContext) -> RiskDecision:
        try:
            return self._check(intent, ctx)
        except Exception as e:  # fail closed: no internal error may approve a trade
            reason = f"internal_error:{type(e).__name__}"
            try:
                self._store.append_audit("internal_error", {
                    "reason": reason, "msg": str(e)[:300],
                    "client_order_id": intent.client_order_id, "symbol": intent.symbol})
            except Exception:  # audit-sink failure must not mask the fail-closed reject
                _log.exception("risk audit write failed while rejecting %s", intent.client_order_id)
            return RiskDecision(RiskVerdict.REJECT, 0, (reason,))

    def _check(self, intent: OrderIntent, ctx: TradeContext) -> RiskDecision:
        acct, sym, sid = intent.account_id, intent.symbol, intent.strategy_id
        held = self._exposure.position_qty(acct, sym)
        lim = self._cfg.limits

        # ---------------------------------------------------------- EXIT path
        if intent.side is Side.SELL:
            if held <= 0:
                return self._reject("short_not_allowed", intent, held=held)
            if ctx.halted:
                return self._reject("halted", intent)
            if intent.qty > held:
                return self._reject("sell_exceeds_position", intent, qty=intent.qty, held=held)
            self._order_strategy[intent.client_order_id] = sid
            return RiskDecision(RiskVerdict.APPROVE, intent.qty, ())

        # ---------------------------------------------------------- ENTRY path (BUY)
        ref_price = intent.limit_price if intent.limit_price is not None else ctx.mark_price
        if ref_price <= 0:
            return self._reject("bad_ref_price", intent, ref_price=ref_price)

        if not self._store.get_switch().armed:
            return self._reject("disarmed", intent)

        st = self._store.get_strategy(sid)
        if st is None:
            return self._reject("strategy_not_registered", intent)
        if not st.enabled:
            return self._reject("strategy_disabled", intent, disabled_reason=st.disabled_reason)
        # Stage gating is LIVE-only (policy): paper/shadow/backtest trade any enabled stage.
        if self._live and not st.stage.is_live:
            return self._reject("stage_shadow_no_live", intent, stage=st.stage.value)

        # Loss checks use realized+unrealized so open-position bleed blocks entries before
        # a fill books it (L-A). Budgets protect real capital -> LIVE only.
        total_u = self._unrealized.total()
        if self._live:
            program = self._program_pnl + total_u
            if program <= -self._cfg.budgets.learning_budget_total:
                return self._reject("learning_budget_exhausted", intent, program_pnl=program)
            strat = self._strategy_pnl.get(sid, 0.0) + self._unrealized.strategy(sid)
            if strat <= -self._cfg.budgets.budget_for(sid):
                return self._reject("strategy_budget_exhausted", intent, strategy_pnl=strat)

        # Daily loss limits and the profit lock are strategy behavior: ALL modes.
        daily_global = self._daily_global_pnl + total_u
        if self._daily_locked_global or daily_global <= -lim.daily_loss_limit_global:
            return self._reject("daily_loss_limit_global", intent, pnl=daily_global)
        daily_acct = self._daily_pnl_by_account.get(acct, 0.0) + self._unrealized.account(acct)
        if acct in self._daily_locked_accounts or daily_acct <= -lim.daily_loss_limit_per_account:
            return self._reject("daily_loss_limit_account", intent, account=acct, pnl=daily_acct)
        if self._profit_lock_threshold is not None and (
                self._profit_locked or self._daily_global_pnl >= self._profit_lock_threshold):
            return self._reject("profit_lock", intent, pnl=self._daily_global_pnl)

        if ctx.halted:
            return self._reject("halted", intent)
        if ctx.session_window not in lim.allowed_windows:
            return self._reject("window_not_allowed", intent, window=ctx.session_window)

        # price / spread / participation / LULD
        if ref_price < lim.min_price:
            return self._reject("min_price", intent, ref_price=ref_price)
        if ctx.spread_pct is not None:
            if ctx.spread_pct > lim.max_spread_pct:
                return self._reject("max_spread_pct", intent, spread_pct=ctx.spread_pct)
        elif ref_price < 1.0:
            return self._reject("unknown_spread_sub_dollar", intent, ref_price=ref_price)
        if ctx.bar_volume is None:
            return self._reject("unknown_volume", intent)
        if ctx.luld_distance_pct is None:
            self._store.append_audit("luld_unknown", {
                "client_order_id": intent.client_order_id, "symbol": sym})
        elif ctx.luld_distance_pct < lim.luld_buffer_pct:
            return self._reject("luld_buffer", intent, luld_distance_pct=ctx.luld_distance_pct)

        if len(self._daily_entry_orders) >= lim.max_trades_per_day:
            return self._reject("max_trades_per_day", intent, count=len(self._daily_entry_orders))
        if held == 0 and self._exposure.open_position_count() >= lim.max_open_positions:
            return self._reject("max_open_positions", intent)

        # entries require a protective stop below the entry
        if intent.stop_price is None:
            return self._reject("entry_without_stop", intent)
        if intent.stop_price >= ref_price:
            return self._reject("invalid_stop", intent, stop_price=intent.stop_price, ref_price=ref_price)

        # ---------------------------------------------------------- reduce pipeline
        qty = intent.qty
        reasons: list[str] = []
        per_share_loss = ref_price - intent.stop_price
        headroom = lim.max_symbol_notional_total - self._exposure.symbol_notional(sym)
        caps: list[tuple[int, str]] = [
            (_cap(lim.max_notional_per_trade, ref_price), "max_notional_per_trade"),
            (_cap(lim.max_loss_per_trade, per_share_loss), "max_loss_per_trade"),
            (int(math.floor(lim.max_participation * ctx.bar_volume + 1e-9)), "max_participation"),
            (_cap(headroom, ref_price) if headroom > 0 else 0, "max_symbol_notional_total"),
        ]
        # Stage-allocation cap is LIVE-only (policy): it sizes real capital at risk.
        if self._live:
            alloc = self._cfg.stages.allocation_for(st.stage) * st.size_multiplier
            caps.append((_cap(alloc, ref_price), "stage_allocation"))
        if self._settlement is not None and self._settlement.is_cash_account(acct):
            caps.append((_cap(self._settlement.available_settled_cash(acct), ref_price), "settled_cash"))

        for max_qty, reason in caps:
            if max_qty < qty:
                if max_qty < 1:
                    return self._reject(reason, intent, max_qty=max_qty)
                qty = max_qty
                reasons.append(reason)

        self._order_strategy[intent.client_order_id] = sid
        if qty < intent.qty:
            self._store.append_audit("reduce", {
                "client_order_id": intent.client_order_id, "symbol": sym,
                "from_qty": intent.qty, "to_qty": qty, "reasons": reasons})
            return RiskDecision(RiskVerdict.REDUCE, qty, tuple(reasons))
        return RiskDecision(RiskVerdict.APPROVE, qty, ())

    def _reject(self, reason: str, intent: OrderIntent, **detail: object) -> RiskDecision:
        self._store.append_audit("reject", {
            "reason": reason, "client_order_id": intent.client_order_id,
            "symbol": intent.symbol, "strategy_id": intent.strategy_id,
            "side": intent.side.value, **detail})
        return RiskDecision(RiskVerdict.REJECT, 0, (reason,))

    # ------------------------------------------------------------------ fills
    def on_fill(self, fill: Fill) -> None:
        sid = self._resolve_strategy(fill.client_order_id)
        pos = self._positions.get((fill.account_id, fill.symbol))
        if fill.side is Side.SELL and pos is not None and pos.qty > 0:
            realized = (fill.price - pos.avg_cost) * min(fill.qty, pos.qty) - fill.fee
        elif fill.side is Side.SELL:
            realized = -fill.fee
        else:
            realized = 0.0  # buy: fees capitalized into cost basis
        # Durable append is the idempotency guard: a replayed fill_id is a no-op.
        if not self._store.append_loss(
                ts=fill.ts, account_id=fill.account_id, strategy_id=sid, symbol=fill.symbol,
                side=fill.side.value, qty=fill.qty, price=fill.price, fee=fill.fee,
                realized_pnl=realized, fill_id=fill.fill_id):
            return
        self._apply(side=fill.side, account_id=fill.account_id, symbol=fill.symbol, sid=sid,
                    qty=fill.qty, price=fill.price, fee=fill.fee, realized=realized,
                    client_order_id=fill.client_order_id, count_entry=True, daily=True)
        if self._settlement is not None:
            self._settlement.record_fill(fill)
        self._run_auto_actions(fill.account_id, sid)

    def _resolve_strategy(self, client_order_id: str) -> str:
        sid = self._order_strategy.get(client_order_id)
        if sid is None and self._strategy_resolver is not None:
            sid = self._strategy_resolver(client_order_id)
        return sid or ""

    def _apply(self, *, side: Side, account_id: str, symbol: str, sid: str, qty: int,
               price: float, fee: float, realized: float, client_order_id: str,
               count_entry: bool, daily: bool) -> None:
        key = (account_id, symbol)
        if side is Side.BUY:
            pos = self._positions.get(key)
            if pos is None:
                self._positions[key] = _Pos(qty, (price * qty + fee) / qty)
            else:
                nq = pos.qty + qty
                self._positions[key] = _Pos(nq, (pos.avg_cost * pos.qty + price * qty + fee) / nq)
            self._pos_strategy[key] = sid
            if count_entry:
                self._daily_entry_orders.add(client_order_id)
        else:
            pos = self._positions.get(key)
            if pos is not None:
                nq = pos.qty - qty
                if nq <= 0:
                    self._positions.pop(key, None)
                    self._pos_strategy.pop(key, None)
                    self._unrealized.remove(account_id, symbol)  # closed: unrealized collapses to realized
                else:
                    self._positions[key] = _Pos(nq, pos.avg_cost)
                    # Partial exit: the sold shares' loss is now realized; drop it from
                    # unrealized immediately (PASS 3 F-1: was double-counted until re-mark).
                    self._unrealized.scale(account_id, symbol, nq / pos.qty)
        self._program_pnl += realized
        self._strategy_pnl[sid] = self._strategy_pnl.get(sid, 0.0) + realized
        if daily:
            self._daily_global_pnl += realized
            self._daily_pnl_by_account[account_id] = \
                self._daily_pnl_by_account.get(account_id, 0.0) + realized

    def _run_auto_actions(self, account_id: str, sid: str) -> None:
        """Realized-PnL auto-actions on every fill. Latches are shared with
        :meth:`flatten_scopes` and idempotent: whichever path first crosses audits once."""
        lim, bud = self._cfg.limits, self._cfg.budgets
        if self._daily_global_pnl <= -lim.daily_loss_limit_global:
            self._latch_daily_global(self._daily_global_pnl)
        acc_pnl = self._daily_pnl_by_account.get(account_id, 0.0)
        if acc_pnl <= -lim.daily_loss_limit_per_account:
            self._latch_daily_account(account_id, acc_pnl)
        if (self._profit_lock_threshold is not None and not self._profit_locked
                and self._daily_global_pnl >= self._profit_lock_threshold):
            self._profit_locked = True
            self._store.append_audit("profit_lock_reached",
                                     {"pnl": self._daily_global_pnl, "threshold": self._profit_lock_threshold})
        # Budget auto-actions protect real capital: LIVE only.
        if not self._live:
            return
        if self._program_pnl <= -bud.learning_budget_total:
            self._latch_program_budget(self._program_pnl)
        if sid and self._strategy_pnl.get(sid, 0.0) <= -bud.budget_for(sid):
            self._latch_strategy_budget(sid)

    # ------------------------------------------------------------------ loss-scope latches / M2M
    def _latch_daily_global(self, pnl: float) -> None:
        if not self._daily_locked_global:
            self._daily_locked_global = True
            self._store.append_audit("daily_loss_limit_global", {"pnl": pnl}, ts=self._last_mark_ts)

    def _latch_daily_account(self, account_id: str, pnl: float) -> None:
        if account_id not in self._daily_locked_accounts:
            self._daily_locked_accounts.add(account_id)
            self._store.append_audit("daily_loss_limit_account", {"account": account_id, "pnl": pnl},
                                     ts=self._last_mark_ts)

    def _latch_program_budget(self, pnl: float) -> None:
        if not self._program_budget_tripped:
            self._program_budget_tripped = True
            self.disarm(actor="system", reason="learning_budget_exhausted")
            self._store.append_audit("learning_budget_exhausted", {"program_pnl": pnl},
                                     ts=self._last_mark_ts)

    def _latch_strategy_budget(self, sid: str) -> None:
        st = self._store.get_strategy(sid)
        if st is not None and st.enabled:
            self.disable_strategy(sid, "strategy_budget_exhausted")

    def mark_to_market(self, account_id: str, symbol: str, price: float, ts: datetime) -> None:
        """Record unrealized PnL = (price - avg_cost) * held qty for one (account, symbol);
        a flat/closed position has its unrealized removed. Never persisted (recomputed from
        marks), so it cannot corrupt the durable realized ledger (L-A)."""
        self._last_mark_ts = ts
        pos = self._positions.get((account_id, symbol))
        if pos is None or pos.qty <= 0:
            self._unrealized.remove(account_id, symbol)
            return
        unreal = (price - pos.avg_cost) * pos.qty
        self._unrealized.set(account_id, symbol, unreal, self._pos_strategy.get((account_id, symbol), ""))

    def flatten_scopes(self) -> frozenset[str]:
        """Loss scopes to flatten given realized+unrealized PnL: {"global"} on a daily-global
        breach (all modes) or a LIVE learning-budget breach; {"account:<id>"} per account past
        its daily limit; {"strategy:<id>"} per LIVE strategy past its budget. A breach latches
        the matching entry lock (audited once per scope/session) and keeps LIVE budget
        auto-disarm/disable. Exits are never affected. Idempotent per scope."""
        breaches = compute_breaches(
            live=self._live, limits=self._cfg.limits, budgets=self._cfg.budgets,
            daily_global_pnl=self._daily_global_pnl, daily_pnl_by_account=self._daily_pnl_by_account,
            program_pnl=self._program_pnl, strategy_pnl=self._strategy_pnl, unrealized=self._unrealized)
        if breaches.daily_global is not None:
            self._latch_daily_global(breaches.daily_global)
        for acct, pnl in breaches.daily_accounts.items():
            self._latch_daily_account(acct, pnl)
        if breaches.program is not None:
            self._latch_program_budget(breaches.program)
        for sid in breaches.strategies:
            self._latch_strategy_budget(sid)
        return breaches.scope_strings()

    def _replay(self) -> None:
        self._positions.clear()
        self._pos_strategy.clear()
        self._unrealized.clear()
        self._program_pnl = 0.0
        self._strategy_pnl.clear()
        for row in self._store.iter_ledger():
            self._apply(side=Side(row.side), account_id=row.account_id, symbol=row.symbol,
                        sid=row.strategy_id, qty=row.qty, price=row.price, fee=row.fee,
                        realized=row.realized_pnl, client_order_id=row.fill_id,
                        count_entry=False, daily=False)

    # ------------------------------------------------------------------ session
    def start_session(self, session_date: date, equity_by_account: Mapping[str, float]) -> None:
        self._daily_global_pnl = 0.0
        self._daily_pnl_by_account = {}
        self._daily_entry_orders = set()
        self._daily_locked_global = False
        self._daily_locked_accounts = set()
        self._profit_locked = False
        total_eq = float(sum(equity_by_account.values()))
        pct = self._cfg.limits.daily_profit_lock_pct
        self._profit_lock_threshold = (pct / 100.0 * total_eq) if pct > 0 and total_eq > 0 else None
        if self._settlement is not None:
            self._settlement.start_session()
        self._store.append_audit("session_start", {
            "session_date": str(session_date), "accounts": list(equity_by_account)})

    # ------------------------------------------------------------------ read-only views
    @property
    def program_realized_pnl(self) -> float:
        return self._program_pnl

    @property
    def daily_global_pnl(self) -> float:
        return self._daily_global_pnl

    def strategy_realized_pnl(self, strategy_id: str) -> float:
        return self._strategy_pnl.get(strategy_id, 0.0)

    def strategy_size_multiplier(self, strategy_id: str) -> float:
        """Persisted size-multiplier for a strategy (1.0 if unknown). The decay ratchet
        reads this at startup so a restart compounds from the applied value, never 1.0."""
        st = self._store.get_strategy(strategy_id)
        return st.size_multiplier if st is not None else 1.0

    def position_qty(self, account_id: str, symbol: str) -> int:
        pos = self._positions.get((account_id, symbol))
        return pos.qty if pos else 0

    # ------------------------------------------------------------------ controls
    def arm(self, actor: str, reason: str) -> None:
        if not actor:
            raise ValueError("arm requires an authenticated actor")
        self._store.set_switch(armed=True, actor=actor, reason=reason)

    def disarm(self, actor: str, reason: str) -> None:
        self._store.set_switch(armed=False, actor=actor, reason=reason)

    def set_strategy_stage(self, strategy_id: str, stage: LadderStage, actor: str) -> None:
        if not actor:
            raise ValueError("stage promotion requires a human actor")
        self._store.upsert_strategy(strategy_id, enabled=True, stage=stage)
        self._store.append_audit("stage_set",
                                 {"strategy_id": strategy_id, "stage": stage.value, "actor": actor})

    def demote(self, strategy_id: str, reason: str) -> None:
        st = self._store.get_strategy(strategy_id)
        if st is None:
            raise KeyError(f"unknown strategy {strategy_id!r}")
        new_stage = st.stage.demoted()
        self._store.upsert_strategy(strategy_id, stage=new_stage)
        self._store.append_audit("strategy_demoted", {
            "strategy_id": strategy_id, "from": st.stage.value, "to": new_stage.value, "reason": reason})

    def disable_strategy(self, strategy_id: str, reason: str) -> None:
        self._store.upsert_strategy(strategy_id, enabled=False, disabled_reason=reason)
        self._store.append_audit("strategy_disabled", {"strategy_id": strategy_id, "reason": reason})

    def set_size_multiplier(self, strategy_id: str, multiplier: float, actor: str = "system") -> None:
        if not 0.0 <= multiplier <= 1.0:
            raise ValueError("size_multiplier must be in [0, 1]")
        self._store.upsert_strategy(strategy_id, size_multiplier=multiplier)
        self._store.append_audit("size_multiplier_set",
                                 {"strategy_id": strategy_id, "multiplier": multiplier, "actor": actor})
