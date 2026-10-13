"""Unrealized-PnL bookkeeping and loss-scope arithmetic for the risk gate
(IMPLEMENTATION_PLAN §1, §11.6).

The capital guardrails must react to an OPEN position bleeding, not only to realized
PnL — otherwise a position can lose far past a daily/budget limit before a single fill
books the loss. The live executors call :meth:`RiskManager.mark_to_market` on every
mark; this module holds the per-(account, symbol) unrealized dollar PnL recomputed from
those marks (:class:`UnrealizedBook`) and the pure arithmetic that decides which loss
scopes have breached (:func:`compute_breaches`).

Nothing here is persisted: unrealized is always recomputed from the latest marks, so a
restart rebuilds it as fresh marks arrive and it can never corrupt the durable realized
ledger. Side effects (audit rows, auto-disarm/disable, entry locks) stay in the manager;
this module is pure so it is trivially testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from sgt.risk.limits import LossBudgets, RiskLimits


@dataclass(slots=True)
class _Entry:
    unrealized: float
    strategy_id: str


class UnrealizedBook:
    """Per-(account, symbol) unrealized dollar PnL, recomputed from marks.

    A position that closes is removed (its unrealized collapses into realized PnL on the
    fill, which the manager's ledger already tracks)."""

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], _Entry] = {}

    def set(self, account_id: str, symbol: str, unrealized: float, strategy_id: str) -> None:
        self._by_key[(account_id, symbol)] = _Entry(float(unrealized), strategy_id or "")

    def remove(self, account_id: str, symbol: str) -> None:
        self._by_key.pop((account_id, symbol), None)

    def scale(self, account_id: str, symbol: str, factor: float) -> None:
        """Scale a position's unrealized PnL (e.g. remaining/previous qty after a partial
        exit) so the sold shares are not counted in both realized and unrealized."""
        e = self._by_key.get((account_id, symbol))
        if e is not None:
            self._by_key[(account_id, symbol)] = _Entry(e.unrealized * factor, e.strategy_id)

    def clear(self) -> None:
        self._by_key.clear()

    def total(self) -> float:
        return sum(e.unrealized for e in self._by_key.values())

    def account(self, account_id: str) -> float:
        return sum(e.unrealized for (acct, _s), e in self._by_key.items() if acct == account_id)

    def strategy(self, strategy_id: str) -> float:
        return sum(e.unrealized for e in self._by_key.values() if e.strategy_id == strategy_id)

    def by_account(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for (acct, _s), e in self._by_key.items():
            out[acct] = out.get(acct, 0.0) + e.unrealized
        return out

    def by_strategy(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for e in self._by_key.values():
            if e.strategy_id:
                out[e.strategy_id] = out.get(e.strategy_id, 0.0) + e.unrealized
        return out


@dataclass(frozen=True, slots=True)
class ScopeBreaches:
    """Loss scopes currently breaching on realized+unrealized PnL.

    Each value is the combined (realized+unrealized) PnL at the point of breach, carried
    so the manager can audit the exact figure. ``program``/``strategies`` are LIVE-only
    (they protect real capital); daily scopes apply in every mode."""

    daily_global: float | None = None
    daily_accounts: dict[str, float] = field(default_factory=dict)
    program: float | None = None
    strategies: dict[str, float] = field(default_factory=dict)

    def scope_strings(self) -> frozenset[str]:
        scopes: set[str] = set()
        if self.daily_global is not None or self.program is not None:
            scopes.add("global")
        scopes.update(f"account:{a}" for a in self.daily_accounts)
        scopes.update(f"strategy:{s}" for s in self.strategies)
        return frozenset(scopes)


def compute_breaches(
    *,
    live: bool,
    limits: RiskLimits,
    budgets: LossBudgets,
    daily_global_pnl: float,
    daily_pnl_by_account: Mapping[str, float],
    program_pnl: float,
    strategy_pnl: Mapping[str, float],
    unrealized: UnrealizedBook,
) -> ScopeBreaches:
    """Decide which loss scopes breach on realized+unrealized PnL (pure)."""
    total_u = unrealized.total()
    by_acct_u = unrealized.by_account()

    daily_global = daily_global_pnl + total_u
    dg = daily_global if daily_global <= -limits.daily_loss_limit_global else None

    accounts: dict[str, float] = {}
    for acct in set(daily_pnl_by_account) | set(by_acct_u):
        combined = daily_pnl_by_account.get(acct, 0.0) + by_acct_u.get(acct, 0.0)
        if combined <= -limits.daily_loss_limit_per_account:
            accounts[acct] = combined

    program: float | None = None
    strategies: dict[str, float] = {}
    if live:
        prog = program_pnl + total_u
        if prog <= -budgets.learning_budget_total:
            program = prog
        by_strat_u = unrealized.by_strategy()
        for sid in set(strategy_pnl) | set(by_strat_u):
            if not sid:
                continue
            combined = strategy_pnl.get(sid, 0.0) + by_strat_u.get(sid, 0.0)
            if combined <= -budgets.budget_for(sid):
                strategies[sid] = combined

    return ScopeBreaches(daily_global=dg, daily_accounts=accounts,
                         program=program, strategies=strategies)
