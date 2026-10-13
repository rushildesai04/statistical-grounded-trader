"""Shared builders for risk tests. Uses the repo-wide fresh_db fixture (tests/conftest.py)."""
from __future__ import annotations

import itertools
from datetime import date, datetime, timezone

import pytest

from sgt.core.interfaces import TradeContext
from sgt.core.types import Fill, Mode, OrderIntent, OrderType, Side, TimeInForce
from sgt.risk import DictExposureView, LadderStage, RiskConfig, RiskManager

NOW = datetime(2026, 10, 12, 19, 30, tzinfo=timezone.utc)

_LIMITS = {
    "max_notional_per_trade": 10_000.0,
    "max_loss_per_trade": 50.0,
    "daily_loss_limit_per_account": 200.0,
    "daily_loss_limit_global": 500.0,
    "daily_profit_lock_pct": 1.0,
    "max_trades_per_day": 3,
    "max_open_positions": 2,
    "max_symbol_notional_total": 5_000.0,
    "min_price": 0.5,
    "max_spread_pct": 1.5,
    "max_participation": 0.1,
    "luld_buffer_pct": 2.0,
    "allowed_windows": ["power_hour"],
    "cash_account": False,
}
_BUDGETS = {"learning_budget_total": 500.0, "per_strategy_budget": 200.0}
_STAGES = {"shadow": 0.0, "micro": 500.0, "small": 2_000.0, "standard": 100_000.0}


def make_config(budget_overrides: dict | None = None, stage_overrides: dict | None = None,
                **limit_overrides) -> RiskConfig:
    lim = {**_LIMITS, **limit_overrides}
    bud = {**_BUDGETS, **(budget_overrides or {})}
    stages = {**_STAGES, **(stage_overrides or {})}
    return RiskConfig.from_dict({"limits": lim, "budgets": bud, "stages": stages})


def make_manager(config: RiskConfig | None = None, exposure: DictExposureView | None = None,
                 *, mode: Mode = Mode.LIVE, armed: bool = True,
                 stage: LadderStage = LadderStage.STANDARD, strategy_id: str = "s1",
                 settlement=None, store=None,
                 namespace: str | None = None) -> tuple[RiskManager, DictExposureView]:
    exp = exposure or DictExposureView()
    m = RiskManager(config or make_config(), exp, mode=mode, settlement=settlement,
                    store=store, namespace=namespace)
    if armed:
        m.arm("rushil", "test")
    if stage is not None:
        m.set_strategy_stage(strategy_id, stage, "rushil")
    m.start_session(date(2026, 10, 12), {"A": 10_000.0, "B": 10_000.0})
    return m, exp


_ids = itertools.count()


def buy(symbol="WFF", qty=100, limit=5.0, stop=4.9, account="A", strategy="s1") -> OrderIntent:
    return OrderIntent(client_order_id=f"sgt-buy-{next(_ids)}", account_id=account, symbol=symbol,
                       side=Side.BUY, qty=qty, order_type=OrderType.LIMIT, limit_price=limit,
                       tif=TimeInForce.DAY, extended_hours=False, strategy_id=strategy,
                       reason="entry", created_at=NOW, stop_price=stop)


def sell(symbol="WFF", qty=100, limit=5.0, account="A", strategy="s1") -> OrderIntent:
    return OrderIntent(client_order_id=f"sgt-sell-{next(_ids)}", account_id=account, symbol=symbol,
                       side=Side.SELL, qty=qty, order_type=OrderType.LIMIT, limit_price=limit,
                       tif=TimeInForce.DAY, extended_hours=False, strategy_id=strategy,
                       reason="exit", created_at=NOW, stop_price=None)


def ctx(window="power_hour", halted=False, spread=1.0, luld=5.0, mark=5.0, volume=1_000_000.0) -> TradeContext:
    return TradeContext(now=NOW, mark_price=mark, session_window=window, halted=halted,
                        spread_pct=spread, luld_distance_pct=luld, stop_price=4.9, bar_volume=volume)


def fill_from(intent: OrderIntent, *, price: float, qty: int | None = None, fill_id: str | None = None,
              fee: float = 0.0) -> Fill:
    return Fill(client_order_id=intent.client_order_id, broker_order_id="b1", account_id=intent.account_id,
                symbol=intent.symbol, side=intent.side, qty=qty or intent.qty, price=price,
                ts=NOW, fill_id=fill_id or f"fill-{next(_ids)}", fee=fee)


@pytest.fixture
def cfg() -> RiskConfig:
    return make_config()
