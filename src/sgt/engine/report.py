"""Markdown summary of a :class:`BacktestResult` (plan §11.2 reporting).

Includes the headline metrics, exit-reason attribution (which exits make/give back
money) and skip-reason counts (what the strategy passed on and why) so a run is
reviewable at a glance without opening a notebook.
"""
from __future__ import annotations

import pandas as pd

from sgt.engine.backtest import BacktestResult


def render_report(result: BacktestResult) -> str:
    s = result.summary
    lines: list[str] = []
    lines.append(f"# Backtest {result.run_id} (`{result.config_hash}`)\n")
    lines.append("## Summary\n")
    lines.append("| metric | value |")
    lines.append("|---|---|")
    lines.append(f"| trades | {s['n_trades']} |")
    lines.append(f"| net pnl | {s['net_pnl']:.2f} |")
    lines.append(f"| win rate | {s['win_rate']:.1%} |")
    lines.append(f"| expectancy (ret/trade) | {s['expectancy']:.5f} |")
    lines.append(f"| profit factor | {s['profit_factor']:.3f} |")
    lines.append(f"| max drawdown ($) | {s['max_drawdown']:.2f} |")
    lines.append("")
    lines.append("## Exit-reason attribution\n")
    lines.append(_exit_table(result.trades))
    lines.append("")
    lines.append("## Candidate skip reasons\n")
    lines.append(_skip_table(result.candidates))
    return "\n".join(lines) + "\n"


def _exit_table(trades: pd.DataFrame) -> str:
    if trades.empty:
        return "_no trades_"
    grp = trades.groupby("exit_reason")["pnl"].agg(["count", "sum", "mean"]).sort_values("sum")
    out = ["| exit reason | n | total pnl | avg pnl |", "|---|---|---|---|"]
    for reason, row in grp.iterrows():
        out.append(f"| {reason} | {int(row['count'])} | {row['sum']:.2f} | {row['mean']:.2f} |")
    return "\n".join(out)


def _skip_table(candidates: pd.DataFrame) -> str:
    if candidates.empty:
        return "_no candidates_"
    taken = int(candidates["taken"].sum())
    out = ["| outcome | count |", "|---|---|", f"| taken | {taken} |"]
    skipped = candidates[~candidates["taken"]]
    for reason, count in skipped["skip_reason"].value_counts().items():
        out.append(f"| skip:{reason} | {int(count)} |")
    return "\n".join(out)
