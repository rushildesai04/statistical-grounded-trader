"""Per-(account, symbol) position + closed-trade bookkeeping for the live executor.

Factored out of :mod:`sgt.live.executor` to keep that module focused on the per-bar
control flow. ``TradeBook`` mirrors the backtest executor's trade accounting exactly
(entry average, exit proceeds, MFE/MAE, bars held, halted-during) so a shadow run and a
backtest produce identical trades; it also keeps the exposure view in sync (what the risk
gate reads) and invokes the closed-trade hook that feeds the decay monitors.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Mapping

from sgt.brokers.costs import CostModel
from sgt.core.types import Bar, Fill, Side
from sgt.risk.exposure import DictExposureView

ClosedTradeHook = Callable[[dict], None]


@dataclass(slots=True)
class _OpenTrade:
    account_id: str
    symbol: str
    entry_ts: datetime
    qty: int = 0
    entry_price_sum: float = 0.0
    entry_fees: float = 0.0
    entry_avg: float = 0.0
    exit_qty: int = 0
    exit_proceeds: float = 0.0
    exit_ts: datetime | None = None
    exit_reason: str = ""
    mfe: float = 0.0
    mae: float = 0.0
    bars_held: int = 0
    halted_during: bool = False
    modeled_slippage: float = 0.0
    realized_slippage: float = 0.0


class TradeBook:
    def __init__(
        self,
        *,
        strategy_id: str,
        exposure: DictExposureView,
        cost_model: CostModel,
        on_closed_trade: ClosedTradeHook | None = None,
    ) -> None:
        self.strategy_id = strategy_id
        self.exposure = exposure
        self.cost_model = cost_model
        self._on_closed_trade = on_closed_trade
        self.trades: list[dict] = []
        self._open: dict[tuple[str, str], _OpenTrade] = {}
        self._pos_qty: dict[tuple[str, str], int] = {}

    # ------------------------------------------------------------------ queries
    def pos_qty(self, aid: str, symbol: str) -> int:
        return self._pos_qty.get((aid, symbol), 0)

    def open_position_count(self) -> int:
        return sum(1 for q in self._pos_qty.values() if q > 0)

    def held_symbols(self) -> set[str]:
        return {sym for (_a, sym), q in self._pos_qty.items() if q > 0}

    # ------------------------------------------------------------------ mutations
    def seed_position(self, aid: str, symbol: str, qty: int, avg_price: float) -> None:
        if qty <= 0:
            return
        self._pos_qty[(aid, symbol)] = int(qty)
        self.exposure.set_position(aid, symbol, int(qty), mark=avg_price)

    def mark_halted(self, symbol: str) -> None:
        for (_aid, sym), tr in self._open.items():
            if sym == symbol:
                tr.halted_during = True

    def record_fill(self, aid: str, fill: Fill, order_reason: Mapping[str, str],
                    *, modeled_slip: float = 0.0, realized_slip: float = 0.0) -> None:
        sym = fill.symbol
        key = (aid, sym)
        qty = self._pos_qty.get(key, 0)
        if fill.side is Side.BUY:
            qty += fill.qty
            tr = self._open.get(key)
            if tr is None:
                tr = _OpenTrade(account_id=aid, symbol=sym, entry_ts=fill.ts)
                self._open[key] = tr
            tr.qty += fill.qty
            tr.entry_price_sum += fill.qty * fill.price
            tr.entry_fees += fill.fee
            tr.entry_avg = tr.entry_price_sum / tr.qty
        else:
            qty -= fill.qty
            tr = self._open.get(key)
            if tr is not None:
                tr.exit_qty += fill.qty
                tr.exit_proceeds += fill.qty * fill.price - fill.fee
                tr.exit_ts = fill.ts
                tr.exit_reason = order_reason.get(fill.client_order_id, tr.exit_reason or "exit")
        tr = self._open.get(key)
        if tr is not None:
            tr.modeled_slippage += modeled_slip
            tr.realized_slippage += realized_slip
        self._pos_qty[key] = max(qty, 0)
        self.exposure.set_position(aid, sym, self._pos_qty[key], mark=fill.price)
        if self._pos_qty[key] <= 0 and key in self._open:
            self._finalize(key)

    def update_excursions(self, bar: Bar, halted: set[str]) -> None:
        for (aid, sym), tr in self._open.items():
            if sym != bar.symbol or tr.entry_avg <= 0:
                continue
            tr.mfe = max(tr.mfe, (bar.high - tr.entry_avg) / tr.entry_avg)
            tr.mae = min(tr.mae, (bar.low - tr.entry_avg) / tr.entry_avg)
            tr.bars_held += 1
            if bar.symbol in halted:
                tr.halted_during = True

    def close_open_positions(self, last_bars: Mapping[str, Bar]) -> None:
        for key in list(self._open):
            _aid, sym = key
            bar = last_bars.get(sym)
            tr = self._open[key]
            if bar is None:
                self._open.pop(key)
                continue
            fee = self.cost_model.fees(tr.qty) if tr.qty > 0 else 0.0
            tr.exit_qty = tr.qty
            tr.exit_proceeds = tr.qty * bar.close - fee
            tr.exit_ts = bar.ts
            tr.exit_reason = "end_of_data"
            self._pos_qty[key] = 0
            self._finalize(key)

    # ------------------------------------------------------------------ internal
    def _finalize(self, key: tuple[str, str]) -> None:
        tr = self._open.pop(key)
        cost = tr.entry_price_sum + tr.entry_fees
        pnl = tr.exit_proceeds - cost
        row = {
            "account_id": tr.account_id, "strategy_id": self.strategy_id, "symbol": tr.symbol,
            "entry_ts": tr.entry_ts, "entry_price": tr.entry_avg, "exit_ts": tr.exit_ts,
            "exit_price": (tr.exit_proceeds / tr.exit_qty) if tr.exit_qty else None,
            "qty": tr.exit_qty or tr.qty, "pnl": pnl, "ret": (pnl / cost) if cost else 0.0,
            "exit_reason": tr.exit_reason.replace("exit:", ""), "mfe": tr.mfe, "mae": tr.mae,
            "bars_held": tr.bars_held, "halted_during": tr.halted_during,
            "modeled_slippage": tr.modeled_slippage, "realized_slippage": tr.realized_slippage}
        self.trades.append(row)
        self._pos_qty[key] = 0
        if self._on_closed_trade is not None:
            self._on_closed_trade(row)
