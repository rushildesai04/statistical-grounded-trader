"""L-A: money guardrails react to OPEN-position bleed, not only realized PnL.

``mark_to_market`` records unrealized PnL and the daily-loss / budget checks plus
``flatten_scopes`` consume realized+unrealized. Entries are blocked at a breached scope
while exits stay approved; budgets react to unrealized in LIVE only."""
from __future__ import annotations

from datetime import date

from sgt.core.types import Mode, RiskVerdict

from .conftest import NOW, buy, ctx, fill_from, make_config, make_manager, sell


def _open(m, exp, *, qty=200, price=5.0, strategy="s1", account="A", symbol="WFF"):
    """Open a real long: run the entry check (so the fill is attributed to the strategy),
    book the fill, and reflect the position in the exposure view the gate reads."""
    b = buy(qty=qty, limit=price, account=account, symbol=symbol, strategy=strategy)
    m.check(b, ctx(mark=price))
    m.on_fill(fill_from(b, price=price))
    exp.set_position(account, symbol, qty, mark=price)


def test_unrealized_breaches_daily_global_blocks_entries_allows_exits():
    cfg = make_config(budget_overrides={"learning_budget_total": 10_000.0,
                                        "per_strategy_budget": 10_000.0},
                      daily_loss_limit_global=500.0)
    m, exp = make_manager(cfg)
    _open(m, exp, qty=200, price=5.0)
    assert m.flatten_scopes() == frozenset()  # no mark yet -> no bleed

    # Mark down: unrealized = (2.4 - 5.0) * 200 = -520 <= -500.
    m.mark_to_market("A", "WFF", 2.4, NOW)
    assert "global" in m.flatten_scopes()
    assert "daily_loss_limit_global" in set(m.check(buy(), ctx()).reasons)
    # Exits are never blocked by a loss scope.
    assert m.check(sell(qty=200), ctx()).verdict is RiskVerdict.APPROVE


def test_unrealized_breaches_per_account_scope():
    cfg = make_config(budget_overrides={"learning_budget_total": 10_000.0,
                                        "per_strategy_budget": 10_000.0},
                      daily_loss_limit_per_account=200.0, daily_loss_limit_global=10_000.0)
    m, exp = make_manager(cfg)
    _open(m, exp, qty=100, price=5.0, account="A")
    m.mark_to_market("A", "WFF", 2.5, NOW)  # (2.5-5.0)*100 = -250 <= -200
    assert "account:A" in m.flatten_scopes()
    assert "daily_loss_limit_account" in set(m.check(buy(account="A"), ctx()).reasons)
    # A different account with no bleed is untouched.
    assert "account:B" not in m.flatten_scopes()


def test_unrealized_breaches_strategy_budget_live_only():
    cfg = make_config(budget_overrides={"learning_budget_total": 10_000.0,
                                        "per_strategy_budget": 200.0},
                      daily_loss_limit_global=10_000.0, daily_loss_limit_per_account=10_000.0)
    # LIVE: unrealized consumes the per-strategy budget -> scope + auto-disable.
    m, exp = make_manager(cfg, mode=Mode.LIVE)
    _open(m, exp, qty=100, price=5.0, strategy="s1")
    m.mark_to_market("A", "WFF", 2.5, NOW)  # -250 <= -200 budget
    assert "strategy:s1" in m.flatten_scopes()
    assert m._store.get_strategy("s1").enabled is False

    # SHADOW: budgets protect real capital only -> no budget scope from unrealized.
    ms, exps = make_manager(cfg, mode=Mode.SHADOW, namespace="shadow-u")
    _open(ms, exps, qty=100, price=5.0, strategy="s1")
    ms.mark_to_market("A", "WFF", 2.5, NOW)
    assert ms.flatten_scopes() == frozenset()
    assert ms._store.get_strategy("s1").enabled is True


def test_learning_budget_uses_unrealized_in_live_only():
    cfg = make_config(budget_overrides={"learning_budget_total": 200.0,
                                        "per_strategy_budget": 10_000.0},
                      daily_loss_limit_global=10_000.0, daily_loss_limit_per_account=10_000.0)
    m, exp = make_manager(cfg, mode=Mode.LIVE)
    _open(m, exp, qty=100, price=5.0)
    m.mark_to_market("A", "WFF", 2.5, NOW)  # program unrealized -250 <= -200
    assert "global" in m.flatten_scopes()
    # learning-budget breach auto-disarms in LIVE (existing semantics preserved).
    assert m._store.get_switch().armed is False


def test_unrealized_removed_when_position_closes():
    cfg = make_config(budget_overrides={"learning_budget_total": 10_000.0,
                                        "per_strategy_budget": 10_000.0},
                      daily_loss_limit_global=10_000.0, daily_loss_limit_per_account=10_000.0)
    m, exp = make_manager(cfg)
    b = buy(qty=100, limit=5.0)
    m.check(b, ctx())
    m.on_fill(fill_from(b, price=5.0))
    m.mark_to_market("A", "WFF", 3.0, NOW)
    assert m._unrealized.total() != 0.0
    # Fully close the position -> unrealized collapses into realized and is dropped.
    s = sell(qty=100, limit=3.0)
    m.on_fill(fill_from(s, price=3.0))
    assert m._unrealized.total() == 0.0
    # A mark on the now-flat position is a no-op.
    m.mark_to_market("A", "WFF", 1.0, NOW)
    assert m._unrealized.total() == 0.0


def test_partial_exit_does_not_double_count_sold_shares():
    """PASS 3 F-1: after a partial SELL the sold shares' loss lives in realized only."""
    cfg = make_config(budget_overrides={"learning_budget_total": 10_000.0,
                                        "per_strategy_budget": 10_000.0},
                      daily_loss_limit_global=250.0, daily_loss_limit_per_account=10_000.0)
    m, exp = make_manager(cfg)
    _open(m, exp, qty=200, price=5.0)
    m.mark_to_market("A", "WFF", 4.0, NOW)          # unrealized -200 (< 250 limit)
    s = sell(qty=100)
    m.check(s, ctx(mark=4.0))
    m.on_fill(fill_from(s, price=4.0))              # realized -100, 100 shares left
    exp.set_position("A", "WFF", 100, mark=4.0)
    # True combined loss is -200 (=-100 realized + -100 unrealized): still inside -250.
    # Before the fix the stale -200 unrealized made it -300 and blocked entries.
    assert "daily_loss_limit_global" not in set(m.check(buy(symbol="OTHER"), ctx()).reasons)
    assert m.flatten_scopes() == frozenset()
