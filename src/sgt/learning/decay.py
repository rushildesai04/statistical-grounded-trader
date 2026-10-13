"""Live decay monitors (plan §11.4) — the "worked for months, then lost thousands"
defense.

Each monitor is a small, pure, stateful object: feed it observations as trades
close and it returns a ``MonitorSignal`` when a deterministic threshold trips, or
``None`` otherwise. Monitors never place orders or touch the DB — the engine/risk
layer applies the action. ``DecaySupervisor`` aggregates the last signal of every
monitor for a strategy and surfaces the most severe action.

All thresholds come straight from plan §11.4. Monitors are seeded where they use
randomness (bootstrap) so a replay reproduces the same signals.
"""
from __future__ import annotations

import enum
from collections import deque
from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np
from scipy import stats

from sgt.learning.validation import bootstrap_ci


class Action(str, enum.Enum):
    FLAG = "flag"        # require re-validation, keep trading
    HALVE = "halve"      # cut size in half
    DEMOTE = "demote"    # drop back to shadow
    HALT = "halt"        # stop live, investigate
    DISABLE = "disable"  # turn the strategy off entirely


# Severity ordering for aggregation (higher == more severe).
_SEVERITY = {
    Action.FLAG: 1,
    Action.HALVE: 2,
    Action.DEMOTE: 3,
    Action.HALT: 4,
    Action.DISABLE: 5,
}


@dataclass(frozen=True, slots=True)
class MonitorSignal:
    action: Action
    monitor: str
    detail: str


class _Monitor:
    name = "monitor"

    def __init__(self) -> None:
        self.last_signal: MonitorSignal | None = None

    def _emit(self, action: Action, detail: str) -> MonitorSignal:
        self.last_signal = MonitorSignal(action=action, monitor=self.name, detail=detail)
        return self.last_signal


class CusumMonitor(_Monitor):
    """One-sided downward CUSUM on per-trade return vs validated expectancy.

    ``k`` (slack) and ``h`` (decision threshold) are in units of ``validated_std``.
    Accumulates standardized shortfall ``S = max(0, S + (expectancy - x)/std - k)``;
    emits DEMOTE and resets when ``S > h``.
    """
    name = "cusum"

    def __init__(self, validated_expectancy: float, validated_std: float, k: float = 0.5, h: float = 5.0) -> None:
        super().__init__()
        if validated_std <= 0:
            raise ValueError("validated_std must be > 0")
        self.expectancy = validated_expectancy
        self.std = validated_std
        self.k = k
        self.h = h
        self.s = 0.0

    def update(self, trade_return: float) -> MonitorSignal | None:
        shortfall = (self.expectancy - trade_return) / self.std
        self.s = max(0.0, self.s + shortfall - self.k)
        if self.s > self.h:
            detail = f"CUSUM {self.s:.2f} > h={self.h} (downward shift vs expectancy {self.expectancy:.5f})"
            self.s = 0.0
            return self._emit(Action.DEMOTE, detail)
        return None


class RollingExpectancyMonitor(_Monitor):
    """Rolling-window expectancy: HALVE when window mean < 0 and its bootstrap upper
    CI is still below the validated expectancy (plan §11.4)."""
    name = "rolling_expectancy"

    def __init__(self, validated_expectancy: float, window: int = 20, alpha: float = 0.05,
                 n_boot: int = 1000, seed: int = 0) -> None:
        super().__init__()
        if window < 2:
            raise ValueError("window must be >= 2")
        self.validated_expectancy = validated_expectancy
        self.window = window
        self.alpha = alpha
        self.n_boot = n_boot
        self.seed = seed
        self._buf: deque[float] = deque(maxlen=window)

    def update(self, trade_return: float) -> MonitorSignal | None:
        self._buf.append(float(trade_return))
        if len(self._buf) < self.window:
            return None
        vals = np.array(self._buf, dtype=float)
        mean = float(vals.mean())
        _, hi = bootstrap_ci(vals, np.mean, n=self.n_boot, alpha=self.alpha, seed=self.seed)
        if mean < 0 and hi < self.validated_expectancy:
            return self._emit(
                Action.HALVE,
                f"rolling mean {mean:.5f} < 0, boot upper {hi:.5f} < validated {self.validated_expectancy:.5f}",
            )
        return None


class DrawdownBudgetMonitor(_Monitor):
    """Drawdown vs allotted budget: HALVE at 50% consumed, DISABLE at 100%.

    ``update`` takes the realized PnL of a closed trade (same units as ``budget``,
    e.g. dollars). Drawdown is measured peak-to-trough on cumulative PnL.
    """
    name = "drawdown_budget"

    def __init__(self, budget: float) -> None:
        super().__init__()
        if budget <= 0:
            raise ValueError("budget must be > 0")
        self.budget = budget
        self.equity = 0.0
        self.peak = 0.0
        self._halved = False

    def update(self, trade_pnl: float) -> MonitorSignal | None:
        self.equity += float(trade_pnl)
        self.peak = max(self.peak, self.equity)
        drawdown = self.peak - self.equity
        consumed = drawdown / self.budget
        if consumed >= 1.0:
            return self._emit(Action.DISABLE, f"drawdown {drawdown:.2f} >= 100% of budget {self.budget:.2f}")
        if consumed >= 0.5 and not self._halved:
            self._halved = True
            return self._emit(Action.HALVE, f"drawdown {drawdown:.2f} >= 50% of budget {self.budget:.2f}")
        return None


class SlippageDriftMonitor(_Monitor):
    """Realized-vs-modeled slippage drift: HALT when the ratio of summed realized to
    summed modeled slippage over the last ``window`` trades exceeds ``threshold``.

    Interface note: unlike the other monitors this ``update`` takes two values
    (realized, modeled) because slippage drift is defined over a pair.
    """
    name = "slippage_drift"

    def __init__(self, window: int = 20, threshold: float = 1.5) -> None:
        super().__init__()
        if window < 1:
            raise ValueError("window must be >= 1")
        self.window = window
        self.threshold = threshold
        self._realized: deque[float] = deque(maxlen=window)
        self._modeled: deque[float] = deque(maxlen=window)

    def update(self, realized_slippage: float, modeled_slippage: float) -> MonitorSignal | None:
        if modeled_slippage < 0 or realized_slippage < 0:
            raise ValueError("slippage values must be >= 0")
        self._realized.append(float(realized_slippage))
        self._modeled.append(float(modeled_slippage))
        if len(self._realized) < self.window:
            return None
        modeled_sum = sum(self._modeled)
        if modeled_sum <= 0:
            return None
        ratio = sum(self._realized) / modeled_sum
        if ratio > self.threshold:
            return self._emit(Action.HALT, f"realized/modeled slippage {ratio:.2f} > {self.threshold}")
        return None


class WinRateMonitor(_Monitor):
    """Binomial test of realized win rate vs validated win rate: DEMOTE when the
    one-sided test (win rate dropped) rejects at ``alpha`` (plan §11.4, p<0.01)."""
    name = "win_rate"

    def __init__(self, validated_win_rate: float, min_trades: int = 20, alpha: float = 0.01) -> None:
        super().__init__()
        if not 0 < validated_win_rate < 1:
            raise ValueError("validated_win_rate must be in (0, 1)")
        self.p0 = validated_win_rate
        self.min_trades = min_trades
        self.alpha = alpha
        self.wins = 0
        self.n = 0

    def update(self, trade_return: float) -> MonitorSignal | None:
        self.n += 1
        if trade_return > 0:
            self.wins += 1
        if self.n < self.min_trades:
            return None
        pvalue = stats.binomtest(self.wins, self.n, self.p0, alternative="less").pvalue
        if pvalue < self.alpha:
            observed = self.wins / self.n
            return self._emit(
                Action.DEMOTE,
                f"win rate {observed:.3f} vs validated {self.p0:.3f}, binomial p={pvalue:.4f} < {self.alpha}",
            )
        return None


class PSIMonitor(_Monitor):
    """Population Stability Index of a live feature vs its reference distribution:
    FLAG when PSI > ``threshold`` (plan §11.4, 0.25 on a top-5 feature).

    Reference bin edges are quantile-based from the reference sample. ``update``
    buffers live values and computes PSI once ``window`` observations accumulate,
    then clears the buffer for the next window.
    """
    name = "psi"

    def __init__(self, reference: Sequence[float], bins: int = 10, window: int | None = None,
                 threshold: float = 0.25) -> None:
        super().__init__()
        ref = np.asarray(reference, dtype=float)
        if ref.size < bins:
            raise ValueError("reference needs at least `bins` observations")
        self.bins = bins
        self.threshold = threshold
        self.window = window or ref.size
        # Quantile edges with open ends so live values outside the reference range still bin.
        qs = np.linspace(0, 1, bins + 1)
        edges = np.quantile(ref, qs)
        edges = np.unique(edges)
        if edges.size < 3:
            raise ValueError("reference distribution is degenerate (not enough distinct values)")
        edges[0], edges[-1] = -np.inf, np.inf
        self._edges = edges
        self._ref_pct = self._hist_pct(ref)
        self._buf: list[float] = []

    def _hist_pct(self, values: np.ndarray) -> np.ndarray:
        counts, _ = np.histogram(values, bins=self._edges)
        pct = counts / counts.sum()
        # Laplace smoothing so empty bins don't blow up the log.
        eps = 1e-6
        return (pct + eps) / (1.0 + eps * len(pct))

    def update(self, value: float) -> MonitorSignal | None:
        self._buf.append(float(value))
        if len(self._buf) < self.window:
            return None
        obs = self._hist_pct(np.asarray(self._buf, dtype=float))
        self._buf = []
        psi = float(np.sum((obs - self._ref_pct) * np.log(obs / self._ref_pct)))
        if psi > self.threshold:
            return self._emit(Action.FLAG, f"PSI {psi:.3f} > {self.threshold}")
        return None


class DecaySupervisor:
    """Aggregates a strategy's monitors and surfaces the single most severe action.

    The caller feeds each monitor its appropriate observation (the monitors have
    different input shapes); the supervisor only aggregates their latest signals.
    """

    def __init__(self, monitors: Mapping[str, _Monitor]) -> None:
        self.monitors = dict(monitors)

    def most_severe(self) -> MonitorSignal | None:
        signals = [m.last_signal for m in self.monitors.values() if m.last_signal is not None]
        if not signals:
            return None
        return max(signals, key=lambda s: _SEVERITY[s.action])

    @staticmethod
    def severity(action: Action) -> int:
        return _SEVERITY[action]
