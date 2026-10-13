"""Candidate card: the human-readable evidence for one searched strategy (plan §10, §12).

Renders params, out-of-sample metrics with bootstrap confidence intervals, the Deflated
Sharpe Ratio with its true trial count, the benchmark comparison and promotion decision,
regime breakdowns (session window, symbol, float bucket), parameter heatmaps and the
lockbox status — everything a reviewer needs to accept or reject a candidate without
opening a notebook. Markdown only; no plotting dependency.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from sgt.core.interfaces import ReferenceData
from sgt.core.types import Bar
from sgt.intraday.calendar import default_calendar
from sgt.learning.validation import AcceptanceReport, bootstrap_ci, evaluate_acceptance
from sgt.search.benchmarks import BenchmarkResult
from sgt.search.runner import SearchResult, TrialResult, evaluate_oos
from sgt.search.select import metric_grid, numeric_axes, render_grid

_WINDOW_PRIORITY = ("power_hour", "opening_hour", "midday", "rth", "after_hours", "pre")


def _sr_variance(result: SearchResult) -> float | None:
    sharpes = [t.metrics.get("sharpe") for t in result.trials]
    vals = [float(s) for s in sharpes if s is not None and math.isfinite(float(s))]
    if len(vals) < 2:
        return None
    return float(np.var(vals, ddof=1))


def _window_label(ts) -> str:
    wins = default_calendar().windows(ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts)
    for w in _WINDOW_PRIORITY:
        if w in wins:
            return w
    return "none"


def _float_bucket(symbol: str, reference: Mapping[str, ReferenceData] | None) -> str:
    if not reference or symbol not in reference or reference[symbol].free_float is None:
        return "unknown"
    f = reference[symbol].free_float
    if f < 10_000_000:
        return "<10M"
    if f < 40_000_000:
        return "10-40M"
    return ">40M"


def _breakdown(trades: pd.DataFrame, key: str) -> str:
    if trades.empty or key not in trades.columns:
        return "_no trades_"
    grp = trades.groupby(key)["ret"].agg(["count", "mean", "sum"]).sort_values("sum", ascending=False)
    out = [f"| {key} | n | mean ret | total ret |", "|---|---|---|---|"]
    for idx, row in grp.iterrows():
        out.append(f"| {idx} | {int(row['count'])} | {row['mean']:.5f} | {row['sum']:.5f} |")
    return "\n".join(out)


def render_candidate_card(
    result: SearchResult,
    trial: TrialResult,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    *,
    benchmark: BenchmarkResult | None = None,
    lockbox: AcceptanceReport | None = None,
    reference: Mapping[str, ReferenceData] | None = None,
    max_dd_budget: float | None = None,
    engine_reference: object = None,
    allow_unknown_float: bool = False,
) -> str:
    """``reference`` (a static symbol->ReferenceData map) only labels float buckets in
    the card; ``engine_reference`` is the POINT-IN-TIME source the re-run backtest uses
    for the universe gate — never pass an as-of-end static map there (look-ahead)."""
    returns = np.asarray(trial.returns, dtype=float)
    n_trials = result.n_trials or len(result.trials)
    lines: list[str] = [f"# Candidate `{trial.config_hash}`  (id: {trial.config.id})\n"]

    lines.append("## Parameters\n")
    lines.append("| axis | value |\n|---|---|")
    for k in sorted(trial.params):
        lines.append(f"| {k} | {trial.params[k]} |")
    lines.append("")

    lines.append("## Out-of-sample metrics\n")
    lines.append("| metric | value |\n|---|---|")
    lines.append(f"| OOS trades | {trial.n_trades} |")
    lines.append(f"| expectancy (ret/trade) | {trial.metrics.get('expectancy', 0.0):.5f} |")
    if returns.size >= 1:
        lo, hi = bootstrap_ci(returns, np.mean, seed=0)
        lines.append(f"| expectancy 95% CI | [{lo:.5f}, {hi:.5f}] |")
    lines.append(f"| profit factor | {trial.metrics.get('profit_factor', 0.0):.3f} |")
    lines.append(f"| win rate | {trial.metrics.get('win_rate', 0.0):.1%} |")
    lines.append(f"| max drawdown (ret) | {trial.metrics.get('max_drawdown', 0.0):.5f} |")
    if returns.size >= 2:
        report = evaluate_acceptance(
            returns, n_trials, sr_variance_across_trials=_sr_variance(result),
            max_dd_budget=max_dd_budget)
        lines.append(f"| Deflated Sharpe (n_trials={n_trials}) | {report.deflated_sharpe:.4f} |")
    lines.append("")

    if benchmark is not None:
        lines.append("## Benchmarks & promotion\n")
        lines.append(f"random entry rate: {benchmark.random_rate:.4f}\n")
        lines.append("| opponent | cand mean | opp mean | Δ 95% CI | beats |")
        lines.append("|---|---|---|---|---|")
        for c in benchmark.decision.comparisons:
            lines.append(
                f"| {c.opponent} | {c.candidate_mean:.5f} | {c.opponent_mean:.5f} "
                f"| [{c.diff_ci[0]:.5f}, {c.diff_ci[1]:.5f}] | {'yes' if c.beats else 'no'} |")
        lines.append(f"\n**Promotion decision: {'PROMOTE' if benchmark.decision.promote else 'HOLD'}**\n")
        for reason in benchmark.decision.reasons:
            lines.append(f"- {reason}")
        lines.append("")

    # Regime breakdowns need per-trade session window / symbol / float bucket.
    oos = evaluate_oos(trial.config, bars_by_symbol, result.fold_sessions,
                       starting_cash=result.starting_cash, run_tag="card", collect_trades=True,
                       reference=engine_reference, allow_unknown_float=allow_unknown_float)
    trades = oos.trades
    if not trades.empty:
        trades = trades.copy()
        trades["window"] = pd.to_datetime(trades["entry_ts"], utc=True).map(_window_label)
        trades["float_bucket"] = trades["symbol"].map(lambda s: _float_bucket(s, reference))
    lines.append("## Regime breakdown — by session window\n")
    lines.append(_breakdown(trades, "window"))
    lines.append("\n## Regime breakdown — by symbol\n")
    lines.append(_breakdown(trades, "symbol"))
    lines.append("\n## Regime breakdown — by float bucket\n")
    lines.append(_breakdown(trades, "float_bucket"))

    axes = numeric_axes(result)
    if len(axes) >= 2:
        lines.append("\n## Heatmap — {} vs {} (expectancy)\n".format(axes[0], axes[1]))
        lines.append(render_grid(metric_grid(result, axes[0], axes[1], "expectancy")))

    lines.append("\n## Lockbox\n")
    if lockbox is None:
        lines.append("_not opened_")
    else:
        lines.append(f"accepted: **{lockbox.accepted}**; DSR {lockbox.deflated_sharpe:.4f}; "
                     f"expectancy {lockbox.expectancy:.5f} CI "
                     f"[{lockbox.expectancy_ci[0]:.5f}, {lockbox.expectancy_ci[1]:.5f}]; "
                     f"PF {lockbox.profit_factor:.3f}; trades {lockbox.n_trades}")
    return "\n".join(lines) + "\n"
