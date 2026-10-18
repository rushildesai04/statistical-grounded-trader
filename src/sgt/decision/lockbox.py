"""One-shot lockbox evaluation of a trained decision model (2026-10-18).

The lockbox is the block of most recent sessions the trainer never saw. This module
replays the final model + chosen policy over EVERY lockbox candidate (not the per-cluster
training cap) in time order, with the constraints a live runner has:

* one open position per symbol: a new entry on a symbol is ignored until the previous
  trade's horizon ``H`` minutes have elapsed (the label's maximum holding time);
* labels are already net of the round-trip cost, so a trade's P&L is
  ``size_fraction * equity * y_net``;
* concurrent exposure is capped at ``portfolio_cap`` of equity: an entry that would push
  open risk above the cap is skipped (mirrors the runtime's at-risk cap).

Every evaluation is recorded in the trial ledger (``kind="lockbox"``) so repeated peeks at
the same lockbox are visible in the lineage instead of silently becoming selection.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from sgt.decision import validation as val
from sgt.decision.policy import PolicyParams


@dataclass
class LockboxResult:
    sessions: list[str]
    n_candidates: int
    n_signals: int                 # candidates the policy wanted to trade
    n_trades: int                  # after one-per-symbol + portfolio cap
    hit_rate: float
    mean_net_return: float         # per trade, net of costs (fraction of notional)
    mean_ci: tuple[float, float]   # 90% cluster-bootstrap CI of the mean
    equity_start: float
    pnl_usd: float
    return_pct: float
    max_drawdown_usd: float
    daily_pnl: dict[str, float] = field(default_factory=dict)
    trades: pd.DataFrame | None = None

    def to_markdown(self) -> str:
        lines = [
            "## Lockbox simulation (never seen in training)",
            "",
            f"Sessions: {self.sessions[0]} .. {self.sessions[-1]} ({len(self.sessions)})"
            if self.sessions else "Sessions: none",
            "",
            "| metric | value |", "|---|---|",
            f"| candidates scored | {self.n_candidates:,} |",
            f"| policy said trade | {self.n_signals:,} |",
            f"| trades taken (1/symbol, cap) | {self.n_trades:,} |",
            f"| hit rate | {self.hit_rate:.1%} |",
            f"| mean net return / trade | {self.mean_net_return:+.4%} |",
            f"| 90% CI of mean | [{self.mean_ci[0]:+.4%}, {self.mean_ci[1]:+.4%}] |",
            f"| P&L on ${self.equity_start:,.0f} | ${self.pnl_usd:+,.2f} ({self.return_pct:+.2%}) |",
            f"| max drawdown | ${self.max_drawdown_usd:,.2f} |",
            "",
            "| session | P&L |", "|---|---|",
        ]
        lines += [f"| {d} | ${v:+,.2f} |" for d, v in self.daily_pnl.items()]
        return "\n".join(lines) + "\n"


def evaluate_lockbox(model, params: PolicyParams, held: pd.DataFrame, *, label_col: str,
                     horizon_min: int, cost_col: str | None, model_cost_col: str | None,
                     equity: float = 100_000.0, session_col: str = "session_date",
                     symbol_col: str = "symbol", ts_col: str = "ts") -> LockboxResult:
    df = val.labeled_rows(held, label_col).sort_values([ts_col, symbol_col]).reset_index(drop=True)
    sessions = sorted(df[session_col].astype(str).unique().tolist())
    if df.empty:
        return LockboxResult(sessions, 0, 0, 0, float("nan"), float("nan"),
                             (float("nan"), float("nan")), equity, 0.0, 0.0, 0.0)
    preds = model.predict_frame(df)
    rt = val.effective_round_trip(df, cost_col, model_cost_col, params.fallback_round_trip)
    mask, size = val._apply_sizing(preds, params, rt)
    ts = pd.to_datetime(df[ts_col], utc=True)
    y = df[label_col].to_numpy(dtype=float)
    hold = pd.Timedelta(minutes=int(horizon_min))

    busy_until: dict[str, pd.Timestamp] = {}
    open_risk: list[tuple[pd.Timestamp, float]] = []   # (exit_ts, size)
    taken = []
    for i in np.flatnonzero(mask):
        t, sym = ts.iloc[i], str(df.at[i, symbol_col])
        if sym in busy_until and t < busy_until[sym]:
            continue
        open_risk = [(e, s) for e, s in open_risk if e > t]
        if sum(s for _, s in open_risk) + size[i] > params.portfolio_cap + 1e-12:
            continue
        busy_until[sym] = t + hold
        open_risk.append((t + hold, float(size[i])))
        taken.append(i)

    trades = df.loc[taken, [ts_col, session_col, symbol_col]].copy()
    trades["size_fraction"] = size[taken]
    trades["net_return"] = y[taken]
    trades["pnl_usd"] = trades["size_fraction"] * equity * trades["net_return"]
    n = len(trades)
    if n:
        clusters = val.cluster_ids(df.loc[taken])
        lo, hi = val.cluster_bootstrap_ci(trades["net_return"].to_numpy(), clusters, alpha=0.10)
        daily = trades.groupby(trades[session_col].astype(str))["pnl_usd"].sum()
        curve = trades["pnl_usd"].cumsum()
        mdd = float((curve.cummax().clip(lower=0) - curve).max())
        pnl = float(trades["pnl_usd"].sum())
        hit = float((trades["net_return"] > 0).mean())
        mean = float(trades["net_return"].mean())
    else:
        lo = hi = mean = hit = float("nan")
        daily, mdd, pnl = pd.Series(dtype=float), 0.0, 0.0
    return LockboxResult(
        sessions=sessions, n_candidates=len(df), n_signals=int(mask.sum()), n_trades=n,
        hit_rate=hit, mean_net_return=mean, mean_ci=(lo, hi), equity_start=equity,
        pnl_usd=pnl, return_pct=pnl / equity, max_drawdown_usd=mdd,
        daily_pnl={d: float(daily.get(d, 0.0)) for d in sessions}, trades=trades)
