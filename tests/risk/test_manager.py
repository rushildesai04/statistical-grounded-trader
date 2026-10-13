"""RiskManager behavior tests. Each guardrail has a blocking test and, where it has a
threshold, a boundary test proving an order exactly at the limit is allowed."""
from __future__ import annotations

from datetime import date

import pytest

from sgt.core.types import Mode, RiskVerdict
from sgt.risk import DictExposureView, LadderStage, RiskManager, RiskStore
from sgt.risk.settlement import SettlementTracker

from .conftest import buy, ctx, fill_from, make_config, make_manager, sell


def _reasons(dec):
    return set(dec.reasons)


# --------------------------------------------------------------------- switch / restart
def test_disarmed_rejects_entries_but_allows_exits():
    m, exp = make_manager(armed=False)
    assert m.check(buy(), ctx()).verdict is RiskVerdict.REJECT
    assert "disarmed" in _reasons(m.check(buy(), ctx()))
    # an exit (SELL reducing a held long) must still be allowed while disarmed
    exp.set_position("A", "WFF", 100, mark=5.0)
    dec = m.check(sell(qty=100), ctx())
    assert dec.verdict is RiskVerdict.APPROVE


def test_switch_forced_disarmed_after_restart():
    m, _ = make_manager(armed=True)  # armed in this "process"
    assert RiskStore().get_switch().armed is True
    # simulate a restart: a fresh manager over the same DB must boot DISARMED
    RiskManager(make_config(), DictExposureView())
    assert RiskStore().get_switch().armed is False
    audits = {k for _, k, _ in RiskStore().iter_audit()}
    assert "switch_forced_disarmed_on_restart" in audits
    # and entries are rejected until a human re-arms
    m2 = RiskManager(make_config(), DictExposureView())
    assert "disarmed" in _reasons(m2.check(buy(), ctx()))


# --------------------------------------------------------------------- strategy gating
def test_unregistered_strategy_rejected():
    m, _ = make_manager(stage=None)  # never registered
    assert "strategy_not_registered" in _reasons(m.check(buy(), ctx()))


def test_shadow_stage_blocks_live_orders():
    m, _ = make_manager(stage=LadderStage.SHADOW)
    assert "stage_shadow_no_live" in _reasons(m.check(buy(), ctx()))


def test_disabled_strategy_rejected():
    m, _ = make_manager()
    m.disable_strategy("s1", "manual")
    assert "strategy_disabled" in _reasons(m.check(buy(), ctx()))


# --------------------------------------------------------------------- window / price / spread / vol / luld
def test_window_not_allowed():
    m, _ = make_manager()
    assert "window_not_allowed" in _reasons(m.check(buy(), ctx(window="midday")))
    assert m.check(buy(), ctx(window="power_hour")).verdict is not RiskVerdict.REJECT


def test_min_price_boundary():
    m, _ = make_manager()
    assert "min_price" in _reasons(m.check(buy(limit=0.4, stop=0.35), ctx(mark=0.4)))
    # exactly at min_price (0.5) is allowed
    assert m.check(buy(limit=0.5, stop=0.45), ctx(mark=0.5)).verdict is not RiskVerdict.REJECT


def test_spread_rules():
    m, _ = make_manager()
    assert m.check(buy(), ctx(spread=1.5)).verdict is not RiskVerdict.REJECT          # boundary ok
    assert "max_spread_pct" in _reasons(m.check(buy(), ctx(spread=1.6)))
    # unknown spread on a sub-dollar name fails closed
    assert "unknown_spread_sub_dollar" in _reasons(
        m.check(buy(limit=0.9, stop=0.85), ctx(mark=0.9, spread=None)))
    # unknown spread above $1 is allowed
    assert m.check(buy(), ctx(spread=None)).verdict is not RiskVerdict.REJECT


def test_unknown_volume_rejects_entry():
    m, _ = make_manager()
    assert "unknown_volume" in _reasons(m.check(buy(), ctx(volume=None)))


def test_luld_rules():
    m, _ = make_manager()
    assert m.check(buy(), ctx(luld=2.0)).verdict is not RiskVerdict.REJECT   # boundary ok
    assert "luld_buffer" in _reasons(m.check(buy(), ctx(luld=1.9)))
    # unknown LULD is allowed but logged
    assert m.check(buy(), ctx(luld=None)).verdict is not RiskVerdict.REJECT
    assert any(k == "luld_unknown" for _, k, _ in RiskStore().iter_audit())


# --------------------------------------------------------------------- stop / loss
def test_entry_without_stop_rejected():
    m, _ = make_manager()
    assert "entry_without_stop" in _reasons(m.check(buy(stop=None), ctx()))


def test_invalid_stop_rejected():
    m, _ = make_manager()
    assert "invalid_stop" in _reasons(m.check(buy(limit=5.0, stop=5.0), ctx()))


def test_reduce_to_fit_max_loss():
    # max_loss 50, per-share loss 0.1 -> max 500 shares; ask 600
    m, _ = make_manager()
    dec = m.check(buy(qty=600, limit=5.0, stop=4.9), ctx())
    assert dec.verdict is RiskVerdict.REDUCE and dec.qty == 500
    assert "max_loss_per_trade" in dec.reasons


def test_max_loss_rejects_when_below_one_share():
    m, _ = make_manager(make_config(max_loss_per_trade=0.05))
    dec = m.check(buy(qty=10, limit=5.0, stop=4.9), ctx())
    assert dec.verdict is RiskVerdict.REJECT and "max_loss_per_trade" in dec.reasons


# --------------------------------------------------------------------- notional / participation / stage
def test_max_notional_boundary_and_reduce():
    m, _ = make_manager(make_config(max_notional_per_trade=1000.0))
    assert m.check(buy(qty=200, limit=5.0), ctx()).verdict is RiskVerdict.APPROVE   # 1000 == limit
    dec = m.check(buy(qty=201, limit=5.0), ctx())
    assert dec.verdict is RiskVerdict.REDUCE and dec.qty == 200
    assert "max_notional_per_trade" in dec.reasons


def test_participation_cap():
    m, _ = make_manager()
    # max_participation 0.1 * 1000 volume = 100 shares
    dec = m.check(buy(qty=150), ctx(volume=1000.0))
    assert dec.verdict is RiskVerdict.REDUCE and dec.qty == 100
    assert "max_participation" in dec.reasons


def test_stage_allocation_and_size_multiplier():
    m, _ = make_manager(stage=LadderStage.MICRO)  # alloc 500 -> 100 shares @5
    assert m.check(buy(qty=100), ctx()).verdict is RiskVerdict.APPROVE
    dec = m.check(buy(qty=101), ctx())
    assert dec.verdict is RiskVerdict.REDUCE and dec.qty == 100 and "stage_allocation" in dec.reasons
    m.set_size_multiplier("s1", 0.5)  # 500*0.5=250 -> 50 shares
    dec2 = m.check(buy(qty=100), ctx())
    assert dec2.verdict is RiskVerdict.REDUCE and dec2.qty == 50


# --------------------------------------------------------------------- trades/day, open positions
def test_max_trades_per_day_boundary():
    m, _ = make_manager()  # max_trades_per_day = 3
    for _ in range(2):
        m.on_fill(fill_from(buy(), price=5.0))
    assert m.check(buy(), ctx()).verdict is not RiskVerdict.REJECT  # 2 taken, 3rd allowed
    m.on_fill(fill_from(buy(), price=5.0))
    assert "max_trades_per_day" in _reasons(m.check(buy(), ctx()))


def test_max_open_positions_boundary():
    exp = DictExposureView({("A", "X"): 10, ("A", "Y"): 10}, {"X": 5.0, "Y": 5.0})
    m, _ = make_manager(exposure=exp)  # max_open_positions = 2
    assert "max_open_positions" in _reasons(m.check(buy(symbol="Z"), ctx()))
    # adding to an already-held symbol is not a new position -> allowed
    exp.set_position("A", "X", 10, mark=5.0)
    assert m.check(buy(symbol="X"), ctx()).verdict is not RiskVerdict.REJECT
    # one open position leaves room for a new one
    exp2 = DictExposureView({("A", "X"): 10}, {"X": 5.0})
    m2, _ = make_manager(exposure=exp2)
    assert m2.check(buy(symbol="Z", strategy="s1"), ctx()).verdict is not RiskVerdict.REJECT


def test_aggregate_symbol_notional_across_two_accounts():
    # A holds 500@5 = 2500, B holds 400@5 = 2000 -> 4500 of 5000 cap, headroom 500 -> 100 shares
    exp = DictExposureView({("A", "WFF"): 500, ("B", "WFF"): 400}, {"WFF": 5.0})
    m, _ = make_manager(exposure=exp)
    assert m.check(buy(symbol="WFF", qty=100, account="A"), ctx()).verdict is RiskVerdict.APPROVE
    dec = m.check(buy(symbol="WFF", qty=101, account="A"), ctx())
    assert dec.verdict is RiskVerdict.REDUCE and dec.qty == 100
    assert "max_symbol_notional_total" in dec.reasons


# --------------------------------------------------------------------- exits
def test_sell_without_position_rejected_as_short():
    m, _ = make_manager()
    assert "short_not_allowed" in _reasons(m.check(sell(qty=100), ctx()))


def test_sell_exceeding_position_rejected():
    exp = DictExposureView({("A", "WFF"): 50}, {"WFF": 5.0})
    m, _ = make_manager(exposure=exp)
    assert "sell_exceeds_position" in _reasons(m.check(sell(qty=100), ctx()))


def test_halted_blocks_both_entries_and_exits():
    exp = DictExposureView({("A", "WFF"): 100}, {"WFF": 5.0})
    m, _ = make_manager(exposure=exp)
    assert "halted" in _reasons(m.check(buy(), ctx(halted=True)))
    assert "halted" in _reasons(m.check(sell(qty=100), ctx(halted=True)))


# --------------------------------------------------------------------- daily loss / profit lock
def _open_and_lose(m, exp, loss_price, qty=200):
    m.on_fill(fill_from(buy(qty=qty, limit=5.0), price=5.0))
    exp.set_position("A", "WFF", qty, mark=5.0)
    # realize a loss on part of the position
    m.on_fill(fill_from(sell(qty=qty // 2, limit=loss_price), price=loss_price))
    exp.set_position("A", "WFF", qty // 2, mark=5.0)


def test_daily_account_loss_blocks_entries_allows_exits():
    exp = DictExposureView()
    m, _ = make_manager(exposure=exp)
    _open_and_lose(m, exp, loss_price=3.0)  # (3-5)*100 = -200 == per-account limit
    assert "daily_loss_limit_account" in _reasons(m.check(buy(account="A"), ctx()))
    # exit of the remaining long is still allowed
    assert m.check(sell(qty=100), ctx()).verdict is RiskVerdict.APPROVE


def test_profit_lock_blocks_entries_not_exits():
    exp = DictExposureView()
    m, _ = make_manager(exposure=exp)
    m.on_fill(fill_from(buy(qty=200, limit=5.0), price=5.0))
    exp.set_position("A", "WFF", 200, mark=5.0)
    # +200 realized == 1% of 20k start-of-day equity -> profit lock
    m.on_fill(fill_from(sell(qty=100, limit=7.0), price=7.0))
    exp.set_position("A", "WFF", 100, mark=5.0)
    assert "profit_lock" in _reasons(m.check(buy(), ctx()))
    assert m.check(sell(qty=100), ctx()).verdict is RiskVerdict.APPROVE


def test_daily_counters_reset_on_new_session():
    exp = DictExposureView()
    m, _ = make_manager(exposure=exp)
    _open_and_lose(m, exp, loss_price=3.0)
    assert "daily_loss_limit_account" in _reasons(m.check(buy(account="A"), ctx()))
    m.start_session(date(2026, 10, 13), {"A": 10_000.0, "B": 10_000.0})
    assert m.check(buy(account="A"), ctx()).verdict is not RiskVerdict.REJECT
    assert m.daily_global_pnl == 0.0


# --------------------------------------------------------------------- budgets
def test_learning_budget_exhaustion_auto_disarms_and_audits():
    exp = DictExposureView()
    m, _ = make_manager(exposure=exp)
    m.on_fill(fill_from(buy(qty=200, limit=5.0), price=5.0))
    exp.set_position("A", "WFF", 200, mark=5.0)
    m.on_fill(fill_from(sell(qty=200, limit=2.5), price=2.5))  # -500 == budget
    assert m.program_realized_pnl <= -500.0
    assert RiskStore().get_switch().armed is False
    kinds = {k for _, k, _ in RiskStore().iter_audit()}
    assert "learning_budget_exhausted" in kinds
    # after auto-disarm, new entries are rejected
    assert "disarmed" in _reasons(m.check(buy(), ctx()))


def test_strategy_budget_exhaustion_disables_strategy():
    # Routed through check() so the fill is attributed to its strategy (real lifecycle).
    exp = DictExposureView()
    m, _ = make_manager(exposure=exp)
    b = buy(qty=100, limit=5.0)
    assert m.check(b, ctx()).verdict is RiskVerdict.APPROVE
    m.on_fill(fill_from(b, price=5.0))
    exp.set_position("A", "WFF", 100, mark=5.0)
    s = sell(qty=100, limit=3.0)
    assert m.check(s, ctx()).verdict is RiskVerdict.APPROVE
    m.on_fill(fill_from(s, price=3.0))  # -200 == per-strategy budget
    st = RiskStore().get_strategy("s1")
    assert st is not None and st.enabled is False
    assert "strategy_disabled" in {k for _, k, _ in RiskStore().iter_audit()}


# --------------------------------------------------------------------- idempotency & fail-closed
def test_on_fill_is_idempotent_on_fill_id():
    exp = DictExposureView()
    m, _ = make_manager(exposure=exp)
    m.on_fill(fill_from(buy(qty=100, limit=5.0), price=5.0, fill_id="buy1"))
    sell_fill = fill_from(sell(qty=100, limit=3.0), price=3.0, fill_id="sell1")
    m.on_fill(sell_fill)
    pnl_after_first = m.program_realized_pnl
    m.on_fill(sell_fill)  # replayed duplicate
    assert m.program_realized_pnl == pnl_after_first
    rows = [r for r in RiskStore().iter_ledger() if r.fill_id == "sell1"]
    assert len(rows) == 1


def test_internal_error_fails_closed():
    class Faulty(DictExposureView):
        def position_qty(self, account_id, symbol):
            raise RuntimeError("exposure backend down")

    m, _ = make_manager(exposure=Faulty())
    dec = m.check(buy(), ctx())
    assert dec.verdict is RiskVerdict.REJECT
    assert dec.reasons and dec.reasons[0].startswith("internal_error:")
    assert "internal_error" in {k for _, k, _ in RiskStore().iter_audit()}


# --------------------------------------------------------------------- settled cash
def test_settled_cash_caps_entry():
    tracker = SettlementTracker()
    tracker.register("A", 400.0)  # only $400 settled -> 80 shares @5
    m, _ = make_manager(settlement=tracker)
    assert m.check(buy(qty=80, limit=5.0), ctx()).verdict is RiskVerdict.APPROVE
    dec = m.check(buy(qty=100, limit=5.0), ctx())
    assert dec.verdict is RiskVerdict.REDUCE and dec.qty == 80 and "settled_cash" in dec.reasons


def test_budgets_are_live_only():
    # learning budget 200 (low), daily limits high so only the budget could fire
    cfg = make_config(budget_overrides={"learning_budget_total": 200.0, "per_strategy_budget": 200.0},
                      daily_loss_limit_global=10_000.0, daily_loss_limit_per_account=10_000.0)
    exp = DictExposureView()
    m, _ = make_manager(cfg, exp, mode=Mode.SHADOW, namespace="shadow")
    m.on_fill(fill_from(buy(qty=200, limit=5.0), price=5.0))
    exp.set_position("A", "WFF", 200, mark=5.0)
    m.on_fill(fill_from(sell(qty=200, limit=4.0), price=4.0))  # -200 realized
    # shadow does not auto-disarm or block on budgets
    assert RiskStore("shadow").get_switch().armed is True
    assert "learning_budget_exhausted" not in {k for _, k, _ in RiskStore("shadow").iter_audit()}
    assert m.check(buy(), ctx()).verdict is not RiskVerdict.REJECT


def test_daily_loss_limit_applies_in_shadow_mode():
    cfg = make_config(daily_loss_limit_global=200.0,
                      budget_overrides={"learning_budget_total": 10_000.0, "per_strategy_budget": 10_000.0})
    exp = DictExposureView()
    m, _ = make_manager(cfg, exp, mode=Mode.SHADOW, namespace="shadow")
    m.on_fill(fill_from(buy(qty=200, limit=5.0), price=5.0))
    exp.set_position("A", "WFF", 200, mark=5.0)
    m.on_fill(fill_from(sell(qty=100, limit=3.0), price=3.0))  # -200 == daily global limit
    exp.set_position("A", "WFF", 100, mark=5.0)
    assert "daily_loss_limit_global" in _reasons(m.check(buy(), ctx()))
    # exits still allowed
    assert m.check(sell(qty=100), ctx()).verdict is RiskVerdict.APPROVE


def test_stage_gating_is_live_only():
    # SHADOW ladder stage would block in LIVE, but paper/shadow only need enabled
    m, _ = make_manager(mode=Mode.PAPER, namespace="paper", stage=LadderStage.SHADOW)
    dec = m.check(buy(), ctx())
    assert dec.verdict is not RiskVerdict.REJECT
    assert "stage_shadow_no_live" not in _reasons(dec)


# --------------------------------------------------------------------- namespace isolation (F-1)
def test_backtest_mode_requires_explicit_namespace():
    with pytest.raises(ValueError):
        RiskManager(make_config(), DictExposureView(), mode=Mode.BACKTEST)
    # explicit namespace is accepted
    RiskManager(make_config(), DictExposureView(), mode=Mode.BACKTEST, namespace="run-42")


def test_paper_losses_do_not_touch_live_state():
    exp = DictExposureView()
    # paper run that blows the budget
    m, _ = make_manager(exposure=exp, mode=Mode.PAPER, namespace="paper")
    m.on_fill(fill_from(buy(qty=200, limit=5.0), price=5.0, fill_id="p-buy"))
    exp.set_position("A", "WFF", 200, mark=5.0)
    m.on_fill(fill_from(sell(qty=200, limit=2.0), price=2.0, fill_id="p-sell"))  # -600
    # the LIVE namespace is completely untouched
    assert list(RiskStore("live").iter_ledger()) == []
    assert RiskStore("live").get_switch().armed is False


def test_namespaces_have_independent_switches():
    live, _ = make_manager(mode=Mode.LIVE, armed=True)
    shadow, _ = make_manager(mode=Mode.SHADOW, namespace="shadow", armed=False)
    assert RiskStore("live").get_switch().armed is True
    assert RiskStore("shadow").get_switch().armed is False
