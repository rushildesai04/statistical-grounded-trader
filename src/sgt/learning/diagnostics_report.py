"""Text recommendations and the markdown report for the learning loop (plan §11.2).

Both are deliberately conservative and rule-based: the loop proposes evidence and
hypotheses for a human to approve (plan §11.3), it never decides to trade. Every
conclusion depends on the cost-model assumptions, so the report states them.
"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd

from sgt.brokers.costs import CostModel
from sgt.learning.diagnostics import (
    BucketSpec,
    CostAttribution,
    bucket_table,
)

# The context buckets the exploration grouped by (column, bins|None for categorical).
DEFAULT_BUCKET_SPECS: tuple[BucketSpec, ...] = (
    ("minute_of_day", [0, 570, 600, 660, 840, 900, 960, 1200]),
    ("price", [0, 1, 5, 20, 100, 1e9]),
    ("rsi", [0, 30, 50, 60, 70, 80, 101]),
    ("ema_spread", [-1, 0.0005, 0.001, 0.0025, 0.005, 1]),
    ("dist_hod", [-100, -5, -2, -0.5, 0, 100]),
    ("gap", [-100, -2, 0, 2, 5, 10, 1000]),
    ("pvt_above", None),
    ("psar_up", None),
    ("free_float", [0, 5e6, 20e6, 40e6, 200e6, 1e13]),
)


def recommendations(
    outcomes: pd.DataFrame,
    stable: pd.DataFrame,
    walkforward: pd.DataFrame,
    *,
    cost_attr: CostAttribution | None = None,
    metric: str = "net30",
    gross_metric: str = "gross30",
    min_n: int = 30,
) -> list[str]:
    """Conservative, explicit, rule-based findings for a human reviewer.

    Rules: NO-TRADE when the gross edge is within round-trip cost; net-negative /
    net-positive overall; stable positive buckets as shadow-only hypotheses;
    explicit flags for unstable pockets and for a learned filter whose top decile
    does not clear zero net.
    """
    recs: list[str] = []
    if outcomes.empty or metric not in outcomes.columns:
        return ["INSUFFICIENT DATA: no forward outcomes to analyze."]

    gross = float(outcomes[gross_metric].mean()) if gross_metric in outcomes.columns else float("nan")
    net = float(outcomes[metric].mean())
    cost = float(outcomes["cost_rt"].mean()) if "cost_rt" in outcomes.columns else float("nan")
    n = int(len(outcomes))

    if np.isfinite(gross) and np.isfinite(cost) and abs(gross) < cost:
        recs.append(
            f"NO-TRADE: gross expectancy ~ 0 and costs exceed it "
            f"(|gross {gross*100:+.3f}%| < round-trip cost {cost*100:.3f}%, n={n}). "
            "The trigger has no edge the cost model lets through; do not trade as-is.")
    elif net <= 0:
        recs.append(
            f"NET-NEGATIVE: gross edge ({gross*100:+.3f}%) does not survive costs "
            f"(net {net*100:+.3f}%, cost {cost*100:.3f}%, n={n}). Do not trade as-is.")
    else:
        recs.append(
            f"NET-POSITIVE overall (net {net*100:+.3f}%, gross {gross*100:+.3f}%, "
            f"cost {cost*100:.3f}%, n={n}). Edge survives costs on average, but still "
            "requires stable buckets and out-of-sample validation before any shadow run.")

    if cost_attr is not None and "take_every" in cost_attr.table.index:
        te = cost_attr.table.loc["take_every"]
        recs.append(
            f"COST ATTRIBUTION (take_every): gross exp {te['gross_exp']*100:+.3f}% over "
            f"{int(te['gross_trades'])} trades, net exp {te['net_exp']*100:+.3f}% "
            f"(cost drag {te['cost_drag']*100:.3f}%/trade).")

    stable_pos = stable[stable["stable"]] if not stable.empty else stable
    if stable_pos.empty:
        recs.append("No stable positive buckets found; no candidate hypotheses this run.")
    else:
        recs.append("CANDIDATE HYPOTHESES (shadow only; not promotions):")
        for _, r in stable_pos.iterrows():
            recs.append(
                f"  - {r['spec']}={r['bucket']}: net h1 {r['net_h1']*100:+.3f}% "
                f"(n={r['n_h1']}), h2 {r['net_h2']*100:+.3f}% (n={r['n_h2']}) — "
                "positive in both halves; validate before shadowing.")

    if not stable.empty:
        flipped = stable[(~stable["stable"])
                         & ((stable["net_h1"] > 0) != (stable["net_h2"] > 0))
                         & (stable[["n_h1", "n_h2"]].max(axis=1) >= min_n)]
        for _, r in flipped.iterrows():
            recs.append(
                f"UNSTABLE: {r['spec']}={r['bucket']} is positive in only one half "
                f"(h1 {r['net_h1']*100:+.3f}% n={r['n_h1']}, h2 {r['net_h2']*100:+.3f}% "
                f"n={r['n_h2']}) — a pocket, not an edge. Do not act.")

    if not walkforward.empty and walkforward["top_net"].notna().any():
        top_mean = float(walkforward["top_net"].mean(skipna=True))
        if top_mean <= 0:
            recs.append(
                f"LEARNED FILTER: top-decile net <= 0 out of sample "
                f"(mean {top_mean*100:+.3f}%). The filter reduces losses but does not "
                "create edge; it is not a reason to trade.")
        else:
            recs.append(
                f"LEARNED FILTER: top-decile net {top_mean*100:+.3f}% out of sample — "
                "promising selection signal; validate on the lockbox before shadowing.")
    else:
        recs.append("LEARNED FILTER: insufficient out-of-sample data to evaluate.")

    return recs


# --------------------------------------------------------------------- markdown


def _md_table(df: pd.DataFrame, *, index: bool = False, floatfmt: str = "{:.4f}") -> str:
    if df.empty:
        return "_(no data)_\n"
    frame = df.reset_index() if index else df.copy()
    headers = [str(c) for c in frame.columns]

    def cell(v: object) -> str:
        if isinstance(v, float):
            return "nan" if np.isnan(v) else floatfmt.format(v)
        return str(v)

    lines = ["| " + " | ".join(headers) + " |",
             "| " + " | ".join("---" for _ in headers) + " |"]
    for _, row in frame.iterrows():
        lines.append("| " + " | ".join(cell(v) for v in row.to_list()) + " |")
    return "\n".join(lines) + "\n"


def _coverage(outcomes: pd.DataFrame) -> dict[str, object]:
    if outcomes.empty:
        return {"symbols": 0, "sessions": 0, "range": "n/a", "signals": 0, "symbol_list": ""}
    sessions = sorted(pd.unique(outcomes["session_date"]))
    syms = sorted(pd.unique(outcomes["symbol"]))
    shown = ", ".join(map(str, syms[:20])) + (" ..." if len(syms) > 20 else "")
    return {"symbols": len(syms), "sessions": len(sessions),
            "range": f"{sessions[0]} .. {sessions[-1]}",
            "signals": int(len(outcomes)), "symbol_list": shown}


def render_report(
    *,
    outcomes: pd.DataFrame,
    cost_model: CostModel,
    recommendations: Sequence[str],
    cost_attr: CostAttribution | None = None,
    stable: pd.DataFrame | None = None,
    walkforward: pd.DataFrame | None = None,
    bucket_specs: Sequence[BucketSpec] = DEFAULT_BUCKET_SPECS,
    horizons: Sequence[int] = (5, 15, 30),
    title: str = "Learning from Results",
    as_of: str = "",
) -> str:
    """Render the full markdown report: coverage, cost assumptions, cost attribution,
    overall outcomes, context buckets, stability, learned-filter WFO, and the
    recommendations. Tolerates empty inputs (renders every section header)."""
    stable = stable if stable is not None else pd.DataFrame()
    walkforward = walkforward if walkforward is not None else pd.DataFrame()
    cov = _coverage(outcomes)
    out: list[str] = [f"# {title}"]
    if as_of:
        out.append(f"_Generated {as_of}_")

    out.append("\n## Data coverage")
    out.append(
        f"- Symbols: {cov['symbols']} ({cov['symbol_list']})\n"
        f"- Sessions: {cov['sessions']}  (range {cov['range']})\n"
        f"- Trigger signals studied: {cov['signals']}")

    out.append("\n## Cost-model assumptions")
    out.append(
        "Conclusions below depend on these conservative half-spread estimates "
        "(fraction of price, adverse per side):")
    hs = pd.DataFrame([
        {"bucket": "< $1", "half_spread": cost_model.hs_under_1},
        {"bucket": "$1-5", "half_spread": cost_model.hs_1_to_5},
        {"bucket": "$5-20", "half_spread": cost_model.hs_5_to_20},
        {"bucket": "> $20", "half_spread": cost_model.hs_over_20},
    ])
    out.append(_md_table(hs))
    out.append(
        f"Low-float (< {cost_model.low_float_shares:,.0f} sh) multiplies the "
        f"half-spread by {cost_model.low_float_mult}; participation drag k="
        f"{cost_model.participation_drag_k}. Round-trip cost per signal is "
        "2 x the entry-bar half-spread.")

    out.append("\n## Cost attribution (gross vs net)")
    if cost_attr is not None:
        out.append(_md_table(cost_attr.table, index=True))
    else:
        out.append("_(not computed)_\n")

    out.append("\n## Overall forward outcomes")
    if outcomes.empty:
        out.append("_(no signals)_\n")
    else:
        rows = []
        for h in horizons:
            gc, nc = f"gross{h}", f"net{h}"
            if nc not in outcomes.columns:
                continue
            rows.append({"horizon": h,
                         "gross_mean": float(outcomes[gc].mean()),
                         "net_mean": float(outcomes[nc].mean()),
                         "net_win": float((outcomes[nc] > 0).mean()),
                         "cost_rt": float(outcomes["cost_rt"].mean())})
        out.append(_md_table(pd.DataFrame(rows)))

    out.append("\n## Context buckets (net by segment)")
    if outcomes.empty:
        out.append("_(no signals)_\n")
    else:
        for by, bins in bucket_specs:
            out.append(f"\n### {by}")
            out.append(_md_table(bucket_table(outcomes, by, bins), index=True))

    out.append("\n## Stability (positive in both halves?)")
    out.append(_md_table(stable))

    out.append("\n## Learned-filter walk-forward")
    out.append(_md_table(walkforward))

    out.append("\n## Recommendations")
    if recommendations:
        out.append("\n".join(f"- {line}" if not line.startswith("  ") else line
                             for line in recommendations))
    else:
        out.append("_(none)_")

    return "\n".join(out) + "\n"
