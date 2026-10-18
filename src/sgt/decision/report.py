"""Markdown report for a decision-model training run (DECISION_MODEL.md §8, §10).

Renders data coverage, effective N and tier, every gate with its numbers, the top model
features, cost sensitivity, the label-shuffle null and the final verdict. Pure function of
the ``TrainResult`` -- no I/O side effects beyond returning the string."""
from __future__ import annotations

from sgt.decision.train import TrainResult


def _check(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def render_report(result: TrainResult) -> str:
    cov = result.coverage
    lines: list[str] = []
    lines.append("# Decision model training report")
    lines.append("")
    lines.append(f"**Verdict: {result.verdict}**")
    lines.append("")

    lines.append("## Data coverage")
    lines.append("")
    lines.append("| Field | Value |")
    lines.append("|---|---|")
    lines.append(f"| Rows (candidates) | {cov.get('n_rows', 0)} |")
    lines.append(f"| Sessions | {cov.get('n_sessions', 0)} |")
    lines.append(f"| Symbols | {cov.get('n_symbols', 0)} |")
    lines.append(f"| Effective N (symbol-session clusters) | {result.n_eff} |")
    lines.append(f"| Complexity tier | {result.tier} |")
    lines.append(f"| OOS folds | {cov.get('n_folds', 0)} |")
    lines.append(f"| OOS trades | {cov.get('n_oos_trades', 0)} |")
    lines.append(f"| Median round-trip cost (measured-else-model) | {cov.get('median_round_trip', 0.0):.5f} |")
    lines.append(f"| Measured-cost coverage | {cov.get('measured_cost_coverage', 0.0):.1%} |")
    lines.append(f"| Trial count (DSR lineage) | {result.n_trials} |")
    disc = cov.get("discovery_sources") or []
    if disc:
        lines.append(f"| Discovery sessions | {disc[0]} .. {disc[-1]} |")
    lines.append("")

    lines.append("## Chosen configuration")
    lines.append("")
    if result.chosen_params is not None:
        p = result.chosen_params
        lines.append(f"- Label: `{result.chosen_label}` (k_tp={p.k_tp}, k_sl={p.k_sl})")
        lines.append(f"- Policy: z={p.z}, h={p.h}, p_min={p.p_min}, "
                     f"f_max={p.f_max}, f_fixed={p.f_fixed}")
        sizing = ("fixed-fractional" if p.n_eff < p.kelly_n_threshold
                  else "shrunk 1/4-Kelly on lower-CI p")
        lines.append(f"- Sizing regime: {sizing} (N={p.n_eff}, threshold={p.kelly_n_threshold})")
        lines.append(f"- Portfolio at-risk cap: {p.portfolio_cap}")
    else:
        lines.append("- No configuration selected (insufficient data).")
    lines.append("")

    lines.append("## Gates")
    lines.append("")
    lines.append("| Gate | Result | Detail |")
    lines.append("|---|---|---|")
    for g in result.gates:
        lines.append(f"| {g.name} | {_check(g.passed)} | {g.detail} |")
    lines.append("")

    lines.append("## Out-of-sample metrics")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Expectancy (per trade, net) | {result.expectancy:+.5f} |")
    lines.append(f"| Expectancy 95% cluster-CI | "
                 f"[{result.expectancy_ci[0]:+.5f}, {result.expectancy_ci[1]:+.5f}] |")
    lines.append(f"| Deflated Sharpe | {result.deflated_sharpe:.3f} |")
    lines.append(f"| AUC (p) | {result.auc:.3f} |")
    lines.append(f"| corr(mu, realized) | {result.mu_corr:.3f} |")
    lines.append(f"| Per-fold expectancy | "
                 f"{[round(e, 5) for e in result.per_fold_expectancy]} |")
    lines.append("")

    lines.append("## Cost sensitivity (+/-50% spread stress)")
    lines.append("")
    lines.append("| Spread multiplier | Expectancy |")
    lines.append("|---|---|")
    for mult in (0.5, 1.0, 1.5):
        lines.append(f"| {mult}x | {result.cost_stress.get(f'exp_{mult}', 0.0):+.5f} |")
    lines.append(f"\nSign stable across the stress: "
                 f"{_check(result.cost_stress.get('sign_stable', False))}")
    lines.append("")

    lines.append("## Label-shuffle null")
    lines.append("")
    sn = result.shuffle_null
    tol = sn.get("auc_tol")
    band = f"0.5 +/- {tol:.3f}" if tol is not None else "~0.5"
    lines.append(f"- Shuffled AUC: {sn.get('auc', float('nan')):.3f} "
                 f"(must be within {band}, data-size-aware)")
    lines.append(f"- Shuffled corr(mu, realized): {sn.get('mu_corr', float('nan')):.3f} "
                 f"(must be ~0)")
    lines.append(f"- Collapses to null: {_check(sn.get('is_null', False))}")
    lines.append("")

    lines.append("## Top model features")
    lines.append("")
    if result.model is not None:
        feats = result.model.metadata.get("feature_list", [])
        lines.append("Selected feature set (tier-capped, stability-selected):")
        lines.append("")
        for f in feats:
            lines.append(f"- `{f}`")
    else:
        lines.append("No final model fit.")
    lines.append("")

    lines.append("## Next steps")
    lines.append("")
    if result.verdict == "PROMOTE-CANDIDATE":
        lines.append("Passes every in-sample/OOS gate. Still requires a one-time lockbox "
                     "engine run and a shadow-trading period before promotion.")
    elif result.verdict == "PROMOTE-CANDIDATE-UNVERIFIED-COSTS":
        lines.append("Passes every gate, but measured-quote coverage is below 50% so the "
                     "edge rests on MODELED costs. Re-run with measured costs (and then the "
                     "lockbox + shadow period) before promotion.")
    elif result.verdict == "INVALID":
        lines.append("Label-shuffle null did NOT collapse -- the pipeline shows residual "
                     "signal under permuted labels (leakage/contamination). Do not trust "
                     "this run; fix the leak before re-training.")
    else:
        failing = [g.name for g in result.gates if not g.passed]
        lines.append(f"NO-TRADE. Failing gates: {', '.join(failing) or 'none'}.")
    lines.append("")
    return "\n".join(lines)
