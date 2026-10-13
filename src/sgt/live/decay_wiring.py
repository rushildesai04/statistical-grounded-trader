"""Wire closed-trade outcomes into the decay monitors and apply their verdict to the
risk layer (plan §11.4).

After every closed trade the live loop calls :meth:`DecayWiring.on_closed_trade` with
the trade's return, realized PnL and (when known) realized-vs-modeled slippage. Each
monitor in the strategy's :class:`~sgt.learning.decay.DecaySupervisor` is fed the
observation shaped for it; the single most-severe signal tripped *this* trade is mapped
to a risk action:

    FLAG    -> journal + alert (no capital change; re-validation required)
    HALVE   -> risk.set_size_multiplier(strategy, current * 0.5)
    DEMOTE  -> risk.demote(strategy)
    DISABLE -> risk.disable_strategy(strategy)
    HALT    -> risk.disarm(namespace)   (stop live, investigate)

Every risk mutation audits itself through the risk store; FLAG and an action summary are
additionally written to the event log so "why did size/stage change?" is answerable.
PSI (feature-drift) monitors are not fed here — they consume feature values, not trade
outcomes — so a supervisor may carry one without this wiring touching it.
"""
from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence

import numpy as np

from sgt.core.interfaces import EventSink
from sgt.core.types import Bar
from sgt.learning.decay import (
    Action,
    CusumMonitor,
    DecaySupervisor,
    DrawdownBudgetMonitor,
    MonitorSignal,
    RollingExpectancyMonitor,
    SlippageDriftMonitor,
    WinRateMonitor,
)
from sgt.live.alerts import AlertManager
from sgt.risk.manager import RiskManager
from sgt.search.registry import DecayBaseline
from sgt.strategy.schema import StrategyConfig

_TRADE_RETURN_MONITORS = frozenset({"cusum", "rolling_expectancy", "win_rate"})


def build_supervisor(baseline: DecayBaseline, *, dd_budget: float | None = None,
                     rolling_window: int = 20, slippage_window: int = 20) -> DecaySupervisor:
    """Full decay supervisor from a validated baseline (plan §11.4 monitor set)."""
    monitors = {
        "cusum": CusumMonitor(baseline.expectancy, baseline.ret_std),
        "rolling_expectancy": RollingExpectancyMonitor(baseline.expectancy, window=rolling_window),
        "win_rate": WinRateMonitor(baseline.win_rate),
        "slippage_drift": SlippageDriftMonitor(window=slippage_window),
    }
    budget = dd_budget if (dd_budget and dd_budget > 0) else baseline.max_dd_budget
    if budget and budget > 0:
        monitors["drawdown_budget"] = DrawdownBudgetMonitor(budget)
    return DecaySupervisor(monitors)


def build_flag_only_supervisor(*, dd_budget: float | None = None,
                               slippage_window: int = 20) -> DecaySupervisor:
    """Baseline-free supervisor: only monitors that need no validated return stats
    (drawdown vs the allotted budget, slippage drift). Paired with ``flag_only`` wiring."""
    monitors: dict = {"slippage_drift": SlippageDriftMonitor(window=slippage_window)}
    if dd_budget and dd_budget > 0:
        monitors["drawdown_budget"] = DrawdownBudgetMonitor(dd_budget)
    return DecaySupervisor(monitors)


def baseline_from_backtest(config: StrategyConfig, bars_by_symbol: Mapping[str, Sequence[Bar]],
                           *, starting_cash: float, dd_budget: float | None = None,
                           run_id: str = "decay-baseline", reference: object = None,
                           allow_unknown_float: bool = False) -> DecayBaseline:
    """Compute a decay baseline from a backtest over provided history (PAPER/SHADOW
    ``--baseline-from-backtest``). Raises if there are too few trades or zero variance."""
    from sgt.engine.backtest import run_backtest
    res = run_backtest(config, bars_by_symbol, starting_cash=starting_cash, run_id=run_id,
                       reference=reference, allow_unknown_float=allow_unknown_float)
    if res.trades.empty or len(res.trades) < 2:
        raise ValueError("baseline_from_backtest needs >= 2 backtest trades")
    rets = res.trades["ret"].to_numpy(dtype=float)
    std = float(rets.std(ddof=1))
    if std <= 0:
        raise ValueError("backtest returns have zero variance; cannot build a decay baseline")
    wr = float((res.trades["pnl"].to_numpy(dtype=float) > 0).mean())
    return DecayBaseline(expectancy=float(rets.mean()), ret_std=std,
                         win_rate=min(0.99, max(0.01, wr)), max_dd_budget=dd_budget,
                         modeled_slippage=None, source="backtest")


class DecayWiring:
    def __init__(
        self,
        risk: RiskManager,
        *,
        event_sink: EventSink,
        alerts: AlertManager,
        actor: str = "decay",
        flag_only: bool = False,
    ) -> None:
        self._risk = risk
        self._sink = event_sink
        self._alerts = alerts
        self._actor = actor
        self._flag_only = flag_only
        self._multiplier: dict[str, float] = {}

    def seed_multiplier(self, strategy_id: str, multiplier: float) -> None:
        """Seed the in-memory HALVE ratchet from the persisted size-multiplier so a restart
        compounds from the value a prior run applied, never silently resetting to 1.0 (L-D).
        Never used to raise the multiplier — HALVE only ever multiplies it down."""
        self._multiplier[strategy_id] = float(multiplier)

    def rehydrate(self, strategy_id: str, supervisor: DecaySupervisor, *,
                  trade_return: float, trade_pnl: float,
                  realized_slippage: float | None = None,
                  modeled_slippage: float | None = None) -> None:
        """Replay one persisted CLOSED trade into the monitors' STATE ONLY (L-B).

        The risk action this trade produced live was already applied and persisted, so no
        action is taken here; this restores CUSUM shortfall, rolling buffers, drawdown
        consumption and win-rate counts after a restart (plan §11.4)."""
        for monitor in supervisor.monitors.values():
            self._feed(monitor, trade_return, trade_pnl, realized_slippage, modeled_slippage)

    def on_closed_trade(
        self,
        strategy_id: str,
        supervisor: DecaySupervisor,
        *,
        ts: datetime,
        trade_return: float,
        trade_pnl: float,
        realized_slippage: float | None = None,
        modeled_slippage: float | None = None,
    ) -> MonitorSignal | None:
        """Feed every monitor and apply the most-severe signal tripped this trade."""
        tripped: list[MonitorSignal] = []
        for monitor in supervisor.monitors.values():
            sig = self._feed(monitor, trade_return, trade_pnl,
                             realized_slippage, modeled_slippage)
            if sig is not None:
                tripped.append(sig)
        if not tripped:
            return None
        signal = max(tripped, key=lambda s: DecaySupervisor.severity(s.action))
        self._apply(strategy_id, signal, ts)
        return signal

    # ------------------------------------------------------------------ feeding
    @staticmethod
    def _feed(monitor, trade_return, trade_pnl, realized_slip, modeled_slip):
        name = getattr(monitor, "name", "")
        if name in _TRADE_RETURN_MONITORS:
            return monitor.update(trade_return)
        if name == "drawdown_budget":
            return monitor.update(trade_pnl)
        if name == "slippage_drift":
            if realized_slip is None or modeled_slip is None:
                return None
            return monitor.update(realized_slip, modeled_slip)
        return None  # psi / unknown monitors are not trade-outcome driven

    # ------------------------------------------------------------------ apply
    def _apply(self, strategy_id: str, signal: MonitorSignal, ts: datetime) -> None:
        action = signal.action
        reason = f"decay:{signal.monitor}:{signal.detail}"
        if self._flag_only and action is not Action.FLAG:
            # No validated baseline: monitors are advisory only, never auto-act on capital.
            self._sink.emit("decay_action", ts, {
                "strategy_id": strategy_id, "action": "flag_only", "intended": action.value,
                "monitor": signal.monitor, "detail": signal.detail})
            self._alerts.alert(f"DECAY ADVISORY {action.value.upper()} {strategy_id}", reason)
            return
        if action is Action.HALVE:
            current = self._multiplier.get(strategy_id, 1.0)
            new_mult = round(current * 0.5, 6)
            self._multiplier[strategy_id] = new_mult
            self._risk.set_size_multiplier(strategy_id, new_mult, actor=self._actor)
        elif action is Action.DEMOTE:
            self._risk.demote(strategy_id, reason)
        elif action is Action.DISABLE:
            self._risk.disable_strategy(strategy_id, reason)
        elif action is Action.HALT:
            self._risk.disarm(actor=self._actor, reason=reason)
        elif action is Action.FLAG:
            self._alerts.alert(f"DECAY FLAG {strategy_id}", reason)
        self._sink.emit("decay_action", ts, {
            "strategy_id": strategy_id, "action": action.value,
            "monitor": signal.monitor, "detail": signal.detail})
        if action is not Action.FLAG:
            self._alerts.alert(f"DECAY {action.value.upper()} {strategy_id}", reason)
