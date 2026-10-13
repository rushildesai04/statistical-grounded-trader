"""Decay + trade-journal wiring for :class:`~sgt.live.runner.LiveRunner` (plan §11.4).

Factored out of :mod:`sgt.live.runner` to keep that module focused on the run
lifecycle (feed/adapter checks, reconciliation, bar loop, shutdown). ``DecayRuntime`` is
a small collaborator the runner OWNS: it builds the per-strategy decay supervisor +
wiring, rehydrates decay state from the persisted trade journal on restart, journals
every closed live trade (so a later restart can rehydrate), and feeds the live decay
monitors after each closed trade.

The runner constructs one ``DecayRuntime`` and delegates its closed-trade hook to it;
the runner keeps thin ``_supervisor`` / ``_decay`` / ``_on_closed_trade`` / ``_feed_decay``
accessors so existing callers (and the live executor's ``on_closed_trade`` callback) are
unchanged. ``BaselineMissingError`` is re-exported from the runner so
``from sgt.live.runner import BaselineMissingError`` keeps working.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Callable, Mapping, Sequence

from sgt.core.interfaces import EventSink
from sgt.core.types import Bar, Mode
from sgt.engine.journal import JournalWriter, read_recent_closed_trades
from sgt.live.alerts import AlertManager
from sgt.live.decay_wiring import (
    DecayWiring,
    baseline_from_backtest,
    build_flag_only_supervisor,
    build_supervisor,
)
from sgt.risk.limits import RiskConfig
from sgt.risk.manager import RiskManager
from sgt.search.registry import get_validated_baseline
from sgt.strategy.schema import StrategyConfig

log = logging.getLogger(__name__)

# Most-recent closed trades replayed into the decay monitors on restart (L-B). Bounds the
# rehydration while comfortably covering the monitors' windows and a drawdown/losing streak.
_DECAY_REPLAY_TRADES = 500


class BaselineMissingError(RuntimeError):
    """LIVE mode cannot start a strategy that has no validated decay baseline."""


class DecayRuntime:
    """Owns the decay supervisor/wiring and the trade journal for one live run."""

    def __init__(
        self,
        *,
        config: StrategyConfig,
        config_hash: str,
        mode: Mode,
        run_id: str,
        risk: RiskManager,
        event_sink: EventSink,
        alerts: AlertManager,
        risk_config: RiskConfig,
        clock: Callable[[], datetime],
        baseline_bars: Mapping[str, Sequence[Bar]] | None = None,
        dd_budget: float | None = None,
        reference: object = None,
        allow_unknown_float: bool = False,
    ) -> None:
        self._config = config
        self._config_hash = config_hash
        self._mode = mode
        self._risk = risk
        self._sink = event_sink
        self._alerts = alerts
        self._risk_config = risk_config
        self._clock = clock
        self._baseline_bars = baseline_bars
        self._dd_budget = dd_budget
        self._reference = reference
        self._allow_unknown_float = allow_unknown_float
        # Journals every live/paper/shadow closed trade so decay state can be rehydrated on
        # restart (L-B); writer is scoped by mode+config_hash to match the rehydration query.
        self._journal = JournalWriter(run_id, mode.value, config_hash)
        self.supervisor = None
        self.decay: DecayWiring | None = None

    # ------------------------------------------------------------------ setup
    def _resolve_dd_budget(self) -> float:
        return float(self._dd_budget or self._risk_config.budgets.budget_for(self._config.id))

    def setup(self) -> None:
        """Build the per-strategy decay supervisor + wiring (plan §11.4). LIVE fails
        closed without a validated baseline; PAPER/SHADOW may compute one from a backtest
        (``baseline_bars``) or else run in FLAG-only (advisory) mode with an alert."""
        from sgt.db.session import get_session
        with get_session() as s:
            baseline = get_validated_baseline(s, self._config_hash)
        dd_budget = self._resolve_dd_budget()
        if baseline is None and self._mode is Mode.LIVE:
            raise BaselineMissingError(
                f"LIVE refuses to start {self._config.id} (config {self._config_hash}): no "
                "validated decay baseline in the strategy registry (need expectancy, "
                "per-trade return std, win rate). Validate via the lockbox first.")
        if baseline is None and self._baseline_bars is not None:
            baseline = baseline_from_backtest(
                self._config, self._baseline_bars, starting_cash=1_000_000.0, dd_budget=dd_budget,
                run_id=f"decay-baseline:{self._config_hash}", reference=self._reference,
                allow_unknown_float=self._allow_unknown_float)
        if baseline is not None:
            self.supervisor = build_supervisor(baseline, dd_budget=dd_budget)
            self.decay = DecayWiring(self._risk, event_sink=self._sink, alerts=self._alerts)
        else:
            self.supervisor = build_flag_only_supervisor(dd_budget=dd_budget)
            self.decay = DecayWiring(self._risk, event_sink=self._sink, alerts=self._alerts,
                                     flag_only=True)
            self._alerts.alert(f"DECAY FLAG-ONLY {self._config.id}",
                               "no validated baseline; decay monitors are advisory only")
        self.rehydrate()

    def rehydrate(self) -> None:
        """Restore decay state across a restart (L-B, L-D). The size-multiplier ratchet is
        seeded from the persisted value so HALVE compounds instead of resetting to 1.0; then
        the strategy's persisted CLOSED trades for this config/namespace are replayed into the
        monitors (state only — their live actions were already applied and persisted). Slippage
        is replayed too, so the slippage-drift monitor survives a restart identically."""
        if self.decay is None or self.supervisor is None:
            return
        self.decay.seed_multiplier(self._config.id,
                                   self._risk.strategy_size_multiplier(self._config.id))
        trades = read_recent_closed_trades(self._config_hash, self._mode.value,
                                           _DECAY_REPLAY_TRADES)
        for row in trades:
            self.decay.rehydrate(self._config.id, self.supervisor,
                                 trade_return=float(row.get("ret") or 0.0),
                                 trade_pnl=float(row.get("pnl") or 0.0),
                                 realized_slippage=row.get("realized_slippage"),
                                 modeled_slippage=row.get("modeled_slippage"))
        if trades:
            self._sink.emit("decay_rehydrated", self._clock(),
                            {"strategy_id": self._config.id, "trades": len(trades)})

    # ------------------------------------------------------------------ closed-trade hook
    def on_closed_trade(self, row: dict) -> None:
        """Closed-trade hook: persist the trade (so a restart can rehydrate decay state)
        then feed the live decay monitors."""
        self.journal_trade(row)
        self.feed(row)

    def journal_trade(self, row: dict) -> None:
        import pandas as pd  # local: keep pandas off the module import path
        try:
            self._journal.write_trades(pd.DataFrame([row]))
        except Exception as exc:  # bounded + alerted: a failed journal only degrades the
            # next restart's decay rehydration; the durable risk ledger is written separately.
            log.warning("trade journal write failed: %s", type(exc).__name__)
            self._alerts.alert("TRADE JOURNAL WRITE FAILED", type(exc).__name__)

    def feed(self, row: dict) -> None:
        if self.decay is None or self.supervisor is None:
            return
        self.decay.on_closed_trade(
            row.get("strategy_id") or self._config.id, self.supervisor,
            ts=row.get("exit_ts") or self._clock(),
            trade_return=float(row.get("ret") or 0.0),
            trade_pnl=float(row.get("pnl") or 0.0),
            realized_slippage=row.get("realized_slippage"),
            modeled_slippage=row.get("modeled_slippage"))
