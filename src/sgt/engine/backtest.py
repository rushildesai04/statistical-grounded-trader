"""Backtest driver (plan §8).

``run_backtest`` merges every symbol's bars (and halt events) into one strictly
time-ordered stream, feeds them through ONE :class:`StrategyEngine` +
:class:`BacktestExecutor`, flattens anything still open at the end of data, and
returns a :class:`BacktestResult` (trades, candidates, equity curve, summary
metrics, config hash, run id, event sink). Given identical inputs the run is
deterministic: all timestamps come from bars and all ids from content hashes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from sgt.brokers.costs import DEFAULT_COST_MODEL, CostModel
from sgt.core.interfaces import EventSink
from sgt.core.types import Bar, HaltEvent, Mode
from sgt.engine.engine import StrategyEngine
from sgt.engine.eventlog import InMemoryEventSink
from sgt.engine.executor import BacktestExecutor
from sgt.learning.validation import expectancy, max_drawdown, profit_factor
from sgt.risk.limits import LossBudgets, RiskConfig, RiskLimits
from sgt.strategy.base import Candidate
from sgt.strategy.loader import config_hash as compute_config_hash
from sgt.strategy.runtime import EntryOverride, MetaScorer
from sgt.strategy.schema import StrategyConfig

_TRADE_COLUMNS = ("symbol", "entry_ts", "entry_price", "exit_ts", "exit_price", "qty", "pnl",
                  "ret", "exit_reason", "mfe", "mae", "bars_held", "halted_during")
_CAND_COLUMNS = ("ts", "symbol", "taken", "skip_reason", "features")


@dataclass(slots=True)
class BacktestResult:
    trades: pd.DataFrame
    candidates: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: dict[str, Any]
    config_hash: str
    run_id: str
    events: EventSink = field(repr=False)


def default_risk_config(config: StrategyConfig) -> RiskConfig:
    """A permissive-but-valid risk config derived from the strategy. Daily loss /
    profit-lock are disabled here; pass an explicit ``risk_config`` to exercise them."""
    big = 1.0e12
    limits = RiskLimits(
        max_notional_per_trade=max(config.sizing.max_notional_usd, 1.0),
        max_loss_per_trade=big,
        daily_loss_limit_per_account=big,
        daily_loss_limit_global=big,
        daily_profit_lock_pct=0.0,
        max_trades_per_day=1_000_000,
        max_open_positions=1_000_000,
        max_symbol_notional_total=big,
        min_price=config.universe.price_min,
        max_spread_pct=config.universe.spread_max_pct,
        max_participation=1.0,
        luld_buffer_pct=0.0,
        allowed_windows=frozenset({"rth", "power_hour", "after_hours"}),
        cash_account=False,
    )
    budgets = LossBudgets(learning_budget_total=big, per_strategy_budget=big)
    return RiskConfig(limits=limits, budgets=budgets)


def run_backtest(
    config: StrategyConfig,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    halts: Sequence[HaltEvent] = (),
    reference: object = None,
    meta_filter: Any = None,
    entry_override: EntryOverride | None = None,
    cost_model: CostModel | None = None,
    starting_cash: float,
    event_sink: EventSink | None = None,
    run_id: str = "backtest",
    risk_config: RiskConfig | None = None,
    entry_timeout_bars: int = 3,
    strict_invariants: bool = False,
    allow_unknown_float: bool = False,
    policy: object = None,
    cost_source: object = None,
) -> BacktestResult:
    cost_model = cost_model or DEFAULT_COST_MODEL
    sink = event_sink if event_sink is not None else InMemoryEventSink()
    cfg_hash = compute_config_hash(config)
    candidates: list[Candidate] = []

    def candidate_sink(c: Candidate) -> None:
        candidates.append(c)
        sink.emit("candidate", c.ts, {"symbol": c.symbol, "taken": c.taken,
                                      "skip_reason": c.skip_reason or ""})

    meta_scorer: MetaScorer | None = meta_filter.score if meta_filter is not None else None
    engine = StrategyEngine(
        config, reference=reference, meta_scorer=meta_scorer, entry_override=entry_override,
        candidate_sink=candidate_sink, allow_unknown_float=allow_unknown_float,
        policy=policy, cost_source=cost_source)
    executor = BacktestExecutor(
        config, engine, risk_config=risk_config or default_risk_config(config),
        cost_model=cost_model, starting_cash=starting_cash, event_sink=sink,
        candidate_sink=candidate_sink, run_id=run_id, config_hash=cfg_hash,
        entry_timeout_bars=entry_timeout_bars, strict_invariants=strict_invariants)

    last_bar: dict[str, Bar] = {}
    for kind, ts, item in _merge(bars_by_symbol, halts):
        if kind == "halt":
            executor.process_halt(item)
        else:
            executor.process_bar(item)
            last_bar[item.symbol] = item

    executor.close_open_positions(last_bar)
    sink.flush()

    trades = pd.DataFrame(executor.trades, columns=list(_TRADE_COLUMNS))
    cand_df = pd.DataFrame(
        [{"ts": c.ts, "symbol": c.symbol, "taken": c.taken,
          "skip_reason": c.skip_reason or "", "features": c.features} for c in candidates],
        columns=list(_CAND_COLUMNS))
    equity = _equity_curve(trades, starting_cash)
    summary = _summary(trades, cfg_hash, run_id)
    return BacktestResult(trades=trades, candidates=cand_df, equity_curve=equity,
                          summary=summary, config_hash=cfg_hash, run_id=run_id, events=sink)


def _merge(bars_by_symbol: Mapping[str, Sequence[Bar]],
           halts: Sequence[HaltEvent]) -> list[tuple[str, datetime, Any]]:
    # (ts, 0=halt before 1=bar at the same instant, symbol) is a total, stable order.
    events: list[tuple[datetime, int, str, str, Any]] = []
    for sym, bars in bars_by_symbol.items():
        for b in bars:
            events.append((b.ts, 1, sym, "bar", b))
    for h in halts:
        events.append((h.ts, 0, h.symbol, "halt", h))
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    return [(kind, ts, item) for ts, _rank, _sym, kind, item in events]


def _equity_curve(trades: pd.DataFrame, starting_cash: float) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame({"ts": [], "equity": []})
    ordered = trades.sort_values("exit_ts", kind="stable")
    equity = starting_cash + ordered["pnl"].cumsum()
    return pd.DataFrame({"ts": ordered["exit_ts"].to_numpy(), "equity": equity.to_numpy()})


def _summary(trades: pd.DataFrame, cfg_hash: str, run_id: str) -> dict[str, Any]:
    n = int(len(trades))
    if n == 0:
        return {"n_trades": 0, "net_pnl": 0.0, "win_rate": 0.0, "expectancy": 0.0,
                "profit_factor": 0.0, "max_drawdown": 0.0, "config_hash": cfg_hash, "run_id": run_id}
    pnl = trades["pnl"].to_numpy(dtype=float)
    ret = trades["ret"].to_numpy(dtype=float)
    return {
        "n_trades": n,
        "net_pnl": float(pnl.sum()),
        "win_rate": float((pnl > 0).mean()),
        "expectancy": float(expectancy(ret)),
        "profit_factor": profit_factor(pnl),
        "max_drawdown": float(max_drawdown(pnl)),
        "config_hash": cfg_hash,
        "run_id": run_id,
    }
