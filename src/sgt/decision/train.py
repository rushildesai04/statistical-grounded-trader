"""Training + honest validation for the decision model (DECISION_MODEL.md §8, §10).

Session-grouped CPCV with an embargo >= H and uniqueness-weighted fitting. The label
choice ``(k_tp, k_sl, H)`` and the policy params ``(z, h, p_min)`` are tuned with NESTED
CV: for every outer fold the config is selected on an inner CPCV of that fold's TRAINING
sessions only, so nothing in the reported out-of-sample set influenced its own selection.

Every distinct configuration evaluated is recorded in the persistent trial ledger (F5);
the Deflated Sharpe gate deflates against the whole lineage (priors + this run). The run
is validated end-to-end on the policy P&L after costs, under a +/-50% cost stress (F1),
with per-fold expectancy sign agreement (F6), cluster-bootstrap CIs (F4) and a
label-shuffle null (F8). Verdict: PROMOTE-CANDIDATE, NO-TRADE, or INVALID.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import pandas as pd

from sgt.decision import trial_ledger, validation as val
from sgt.decision.models import DecisionModel, effective_n, select_tier
from sgt.decision.policy import PolicyParams
from sgt.decision.validation import Gate

_LABEL_RE = re.compile(r"^y_net__tp([0-9.]+)_sl([0-9.]+)_h([0-9]+)$")
_log = logging.getLogger(__name__)


def _degenerate_labels(df: pd.DataFrame, col: str) -> bool:
    """True if the FINITE labels for this config carry fewer than two distinct signs (F3).

    Session-truncated trades hold NaN labels and are dropped before fitting; computing the
    single-class guard on the raw column counts NaN as its own ``np.sign`` value, so a fold
    whose only real labels are one-sided looked multi-class and crashed the fit. The guard
    must see only labeled rows."""
    y = df[col].to_numpy(dtype=float)
    y = y[np.isfinite(y)]
    return y.size == 0 or len(np.unique(np.sign(y))) < 2


@dataclass(frozen=True, slots=True)
class LabelSpec:
    col: str
    k_tp: float
    k_sl: float
    H: int
    uniq_col: str


def discover_labels(columns: Sequence[str]) -> list[LabelSpec]:
    specs: list[LabelSpec] = []
    cols = set(columns)
    for c in sorted(columns):
        m = _LABEL_RE.match(c)
        if not m:
            continue
        uniq = c.replace("y_net__", "uniq_w__", 1)
        specs.append(LabelSpec(c, float(m.group(1)), float(m.group(2)), int(m.group(3)),
                               uniq if uniq in cols else ""))
    if not specs:
        raise ValueError("no y_net__tp*_sl*_h* label columns found in dataset")
    return specs


@dataclass(frozen=True, slots=True)
class TrainConfig:
    feature_prefix: str = "f_"
    symbol_col: str = "symbol"
    session_col: str = "session_date"
    cost_col: str = "cost_rt_measured"
    model_cost_col: str = "cost_rt_model"
    n_groups: int = 5
    k_test: int = 1
    inner_groups: int = 4
    inner_k: int = 1
    embargo: int = 1
    z_grid: tuple[float, ...] = (0.5, 1.0)
    h_grid: tuple[float, ...] = (0.0,)
    p_min_grid: tuple[float, ...] = (0.5,)
    f_max: float = 0.02
    f_fixed: float = 0.005
    portfolio_cap: float = 0.06
    kelly_n_threshold: int = 1000
    min_fold_trades: int = 10
    min_inner_trades: int = 10
    dsr_threshold: float = 0.95
    run_id: str = "decision-run"
    seed: int = 0
    splits_override: tuple[tuple[tuple, tuple], ...] | None = None


@dataclass
class TrainResult:
    verdict: str
    gates: list[Gate]
    tier: str
    n_eff: int
    chosen_label: str
    chosen_params: PolicyParams | None
    n_trials: int
    oos_returns: np.ndarray
    expectancy: float
    expectancy_ci: tuple[float, float]
    deflated_sharpe: float
    auc: float
    mu_corr: float
    per_fold_expectancy: list[float]
    cost_stress: dict
    shuffle_null: dict
    coverage: dict
    model: DecisionModel | None = None
    report: dict = field(default_factory=dict)


def feature_columns(df: pd.DataFrame, tc: TrainConfig) -> list[str]:
    return [c for c in df.columns if c.startswith(tc.feature_prefix)]


def _measured_coverage(df: pd.DataFrame, cost_col: str) -> float:
    """Fraction of rows carrying a finite MEASURED round-trip cost (F2). 0 if the column
    is absent -- the run then has no measured-cost backing at all."""
    if cost_col not in df.columns or len(df) == 0:
        return 0.0
    meas = df[cost_col].to_numpy(dtype=float)
    return float(np.isfinite(meas).mean())


def _policy_params(spec: LabelSpec, z: float, h: float, p_min: float,
                   n_eff: int, tc: TrainConfig, rt: float) -> PolicyParams:
    return PolicyParams(
        k_tp=spec.k_tp, k_sl=spec.k_sl, n_eff=n_eff, z=z, h=h, p_min=p_min,
        f_max=tc.f_max, f_fixed=tc.f_fixed, kelly_fraction=0.25,
        kelly_n_threshold=tc.kelly_n_threshold, portfolio_cap=tc.portfolio_cap,
        fallback_round_trip=rt)


def _fit_model(train_df, feature_cols, spec: LabelSpec, n_eff, tc, cfg_hash, seed) -> DecisionModel:
    return DecisionModel().fit(
        val.labeled_rows(train_df, spec.col), feature_cols, spec.col, symbol_col=tc.symbol_col,
        session_col=tc.session_col, weight_col=(spec.uniq_col or None),
        config_hash=cfg_hash, seed=seed)


def _config_dict(spec, z, h, p_min, tc) -> dict:
    return {"label": spec.col, "z": z, "h": h, "p_min": p_min,
            "f_max": tc.f_max, "f_fixed": tc.f_fixed}


def _inner_select(train_df, feature_cols, specs, n_eff, tc, rt, session, seed):
    """Pick (label, z, h, p_min) by pooled inner-CPCV expectancy on training sessions only."""
    inner_sessions = sorted(train_df[tc.session_col].unique())
    groups = min(tc.inner_groups, len(inner_sessions))
    if groups <= tc.inner_k:
        groups = tc.inner_k + 1
    try:
        splits = val.session_cpcv(inner_sessions, groups, tc.inner_k, tc.embargo)
    except ValueError:
        splits = []
    best = None
    for spec in specs:
        fold_models = []
        for tr_s, te_s in splits:
            tr = val.rows_for_sessions(train_df, tr_s, tc.session_col)
            te = val.rows_for_sessions(train_df, te_s, tc.session_col)
            if tr.empty or te.empty or _degenerate_labels(tr, spec.col):
                continue
            try:
                m = _fit_model(tr, feature_cols, spec, n_eff, tc, "inner", seed)
            except ValueError:
                continue
            fold_models.append((m, te))
        for z in tc.z_grid:
            for h in tc.h_grid:
                for pm in tc.p_min_grid:
                    pp = _policy_params(spec, z, h, pm, n_eff, tc, rt)
                    rets = []
                    for m, te in fold_models:
                        r, _ = val.policy_trade_returns(
                            m, pp, te, label_col=spec.col, cost_col=tc.cost_col,
                            model_cost_col=tc.model_cost_col)
                        rets.append(r)
                    allr = np.concatenate(rets) if rets else np.array([])
                    ch = trial_ledger.config_hash(_config_dict(spec, z, h, pm, tc))
                    if session is not None:
                        sr = (float(allr.mean() / allr.std(ddof=1))
                              if allr.size > 1 and allr.std(ddof=1) > 0 else None)
                        trial_ledger.record_trial(
                            session, run_id=tc.run_id,
                            params=_config_dict(spec, z, h, pm, tc),
                            metrics={"sharpe": sr, "n": int(allr.size)}, cfg_hash=ch)
                    score = (float(allr.mean()) if allr.size >= tc.min_inner_trades
                             else -np.inf)
                    if best is None or score > best[0]:
                        best = (score, spec, z, h, pm)
    if best is None:
        return specs[0], tc.z_grid[0], tc.h_grid[0], tc.p_min_grid[0]
    return best[1], best[2], best[3], best[4]


def _outer_splits(df, tc):
    if tc.splits_override is not None:
        return list(tc.splits_override)
    sessions = sorted(df[tc.session_col].unique())
    groups = min(tc.n_groups, len(sessions))
    if groups <= tc.k_test:
        groups = tc.k_test + 1
    return val.session_cpcv(sessions, groups, tc.k_test, tc.embargo)


def train_decision_model(df: pd.DataFrame, tc: TrainConfig, *, session=None,
                         fit_final: bool = True) -> TrainResult:
    """Run the full nested-CPCV training + validation and return the verdict."""
    feature_cols = feature_columns(df, tc)
    if not feature_cols:
        raise ValueError(f"no feature columns with prefix {tc.feature_prefix!r}")
    specs = discover_labels(df.columns)
    n_eff = effective_n(df, tc.symbol_col, tc.session_col)
    tier = select_tier(n_eff)
    # Round-trip cost fallback = median of the per-row effective cost (measured-else-model,
    # F2) so the hurdle / cost stress never collapse to 0 when only model costs exist.
    eff_cost = val.effective_round_trip(df, tc.cost_col, tc.model_cost_col, 0.0)
    rt = float(np.median(eff_cost)) if eff_cost.size else 0.0
    if not np.isfinite(rt):
        rt = 0.0
    measured_coverage = _measured_coverage(df, tc.cost_col)

    if session is not None:
        trial_ledger.seed_prior_trials(session)

    splits = _outer_splits(df, tc)
    folds = []           # (model, params, test_df, label_col)
    pooled_r, pooled_c = [], []
    per_fold_exp = []
    aucs, corrs = [], []
    chosen_counter: dict = {}
    skipped_folds: list[dict] = []   # F3: degenerate folds skipped (never crash the run)
    for i, (tr_s, te_s) in enumerate(splits):
        tr = val.rows_for_sessions(df, tr_s, tc.session_col)
        te = val.rows_for_sessions(df, te_s, tc.session_col)
        if tr.empty or te.empty:
            continue
        # F3: the policy params for this fold (n_eff drives sizing regime + the Wald CI)
        # must come from THIS fold's training sessions only, never the whole dataset.
        fold_n_eff = effective_n(tr, tc.symbol_col, tc.session_col)
        spec, z, h, pm = _inner_select(tr, feature_cols, specs, fold_n_eff, tc, rt, session,
                                       tc.seed + i)
        if _degenerate_labels(tr, spec.col):
            skipped_folds.append({"fold": i, "label": spec.col, "reason": "single_class_labels"})
            _log.info("skipping outer fold %d: finite labels for %s are single-class", i, spec.col)
            continue
        pp = _policy_params(spec, z, h, pm, fold_n_eff, tc, rt)
        cfg = _config_dict(spec, z, h, pm, tc)
        try:
            # F3: a fold that is degenerate in a way the sign guard cannot see (e.g. the
            # win/loss class collapses after uniqueness weighting) must be skipped and
            # counted, not crash the whole run.
            model = _fit_model(tr, feature_cols, spec, fold_n_eff, tc,
                               trial_ledger.config_hash(cfg), tc.seed + i)
        except ValueError as exc:
            skipped_folds.append({"fold": i, "label": spec.col, "reason": str(exc)})
            _log.warning("skipping outer fold %d (%s): %s", i, spec.col, exc)
            continue
        r, c = val.policy_trade_returns(model, pp, te, label_col=spec.col, cost_col=tc.cost_col,
                                        model_cost_col=tc.model_cost_col)
        diag = val.fold_diagnostics(model, te, spec.col)
        folds.append((model, pp, te, spec.col, tuple(tr_s), tuple(te_s)))
        chosen_counter[(spec.col, z, h, pm)] = chosen_counter.get((spec.col, z, h, pm), 0) + 1
        if r.size >= 1:
            pooled_r.append(r)
            pooled_c.append(c)
            per_fold_exp.append(float(r.mean()))
        if np.isfinite(diag["auc"]):
            aucs.append(diag["auc"])
        if np.isfinite(diag["mu_corr"]):
            corrs.append(diag["mu_corr"])

    returns = np.concatenate(pooled_r) if pooled_r else np.array([])
    clusters = np.concatenate(pooled_c) if pooled_c else np.array([])
    coverage = {
        "n_rows": int(len(df)), "n_sessions": int(df[tc.session_col].nunique()),
        "n_symbols": int(df[tc.symbol_col].nunique()), "n_folds": len(folds),
        "n_skipped_folds": len(skipped_folds), "skipped_folds": skipped_folds,
        "n_oos_trades": int(returns.size), "median_round_trip": rt,
        "measured_cost_coverage": measured_coverage,
        "discovery_sources": sorted(df["discovered_session"].dropna().unique().tolist())
        if "discovered_session" in df.columns else [],
    }

    n_trials = trial_ledger.trial_count(session) if session is not None else max(1, len(chosen_counter))
    sr_var = trial_ledger.sharpe_variance_across_trials(session) if session is not None else None

    result = _assemble(df, tc, specs, tier, n_eff, folds, returns, clusters,
                       per_fold_exp, aucs, corrs, n_trials, sr_var, chosen_counter,
                       feature_cols, rt, coverage, fit_final)
    return result


def _assemble(df, tc, specs, tier, n_eff, folds, returns, clusters, per_fold_exp,
              aucs, corrs, n_trials, sr_var, chosen_counter, feature_cols, rt,
              coverage, fit_final) -> TrainResult:
    gates: list[Gate] = []
    chosen = max(chosen_counter, key=chosen_counter.get) if chosen_counter else None
    chosen_label = chosen[0] if chosen else ""
    chosen_spec = next((s for s in specs if s.col == chosen_label), specs[0])
    chosen_params = (_policy_params(chosen_spec, chosen[1], chosen[2], chosen[3], n_eff, tc, rt)
                     if chosen else None)

    exp = float(returns.mean()) if returns.size else 0.0
    auc = float(np.mean(aucs)) if aucs else float("nan")
    mu_corr = float(np.mean(corrs)) if corrs else float("nan")

    if returns.size >= 2:
        ci = val.cluster_bootstrap_ci(returns, clusters, seed=tc.seed)
        dsr, dsr_pass = val.deflated_sharpe_gate(returns, n_trials, sr_var,
                                                 threshold=tc.dsr_threshold)
    else:
        ci, dsr, dsr_pass = (0.0, 0.0), 0.0, False

    gates.append(Gate("min_oos_trades", returns.size >= tc.min_fold_trades,
                      f"{int(returns.size)} OOS trades (need >= {tc.min_fold_trades})"))
    gates.append(Gate("expectancy_ci_lower_positive", ci[0] > 0,
                      f"expectancy {exp:+.5f}, 95% cluster-CI [{ci[0]:+.5f}, {ci[1]:+.5f}]"))
    sign_ok = val.sign_consistent_positive(per_fold_exp)
    gates.append(Gate("per_fold_expectancy_sign", sign_ok,
                      f"per-fold expectancy {[round(e, 5) for e in per_fold_exp]}"))
    gates.append(Gate("deflated_sharpe", dsr_pass,
                      f"DSR {dsr:.3f} > {tc.dsr_threshold} (n_trials={n_trials})"))
    gates.append(Gate("auc_above_half", np.isfinite(auc) and auc > 0.5,
                      f"OOS AUC {auc:.3f}"))

    cost_stress = _cost_stress(folds, tc)
    gates.append(Gate("cost_stress_sign_stable", cost_stress["sign_stable"],
                      f"expectancy @0.5x={cost_stress['exp_0.5']:+.5f}, "
                      f"@1.5x={cost_stress['exp_1.5']:+.5f}"))

    shuffle_null = _shuffle_null(df, tc, folds, feature_cols)
    null_ok = shuffle_null["is_null"]
    gates.append(Gate("label_shuffle_null", null_ok,
                      f"shuffled AUC {shuffle_null['auc']:.3f}, "
                      f"mu_corr {shuffle_null['mu_corr']:.3f}"))

    if not null_ok:
        verdict = "INVALID"
    elif all(g.passed for g in gates):
        verdict = "PROMOTE-CANDIDATE"
    else:
        verdict = "NO-TRADE"

    # F2: a PROMOTE verdict earned mostly on MODELED (not measured) costs cannot be
    # trusted for promotion. Cap it until measured-quote coverage reaches 50%; the run is
    # still a candidate, flagged for a re-run with measured costs before promotion.
    if verdict == "PROMOTE-CANDIDATE" and coverage.get("measured_cost_coverage", 0.0) < 0.5:
        verdict = "PROMOTE-CANDIDATE-UNVERIFIED-COSTS"

    model = None
    if fit_final and chosen and len(np.unique(np.sign(df[chosen_label]))) >= 2:
        cfg = _config_dict(chosen_spec, chosen[1], chosen[2], chosen[3], tc)
        drange = (str(df[tc.session_col].min()), str(df[tc.session_col].max()))
        model = DecisionModel().fit(
            val.labeled_rows(df, chosen_label), feature_cols, chosen_label, symbol_col=tc.symbol_col,
            session_col=tc.session_col, weight_col=(chosen_spec.uniq_col or None),
            data_range=drange, config_hash=trial_ledger.config_hash(cfg),
            trial_ids=[trial_ledger.config_hash(cfg)], seed=tc.seed)

    return TrainResult(
        verdict=verdict, gates=gates, tier=tier.name, n_eff=n_eff,
        chosen_label=chosen_label, chosen_params=chosen_params, n_trials=n_trials,
        oos_returns=returns, expectancy=exp, expectancy_ci=ci, deflated_sharpe=dsr,
        auc=auc, mu_corr=mu_corr, per_fold_expectancy=per_fold_exp,
        cost_stress=cost_stress, shuffle_null=shuffle_null, coverage=coverage,
        model=model)


def _cost_stress(folds, tc) -> dict:
    out = {}
    exps = {}
    for mult in (0.5, 1.0, 1.5):
        rs = []
        for model, pp, te, label, _tr_s, _te_s in folds:
            r, _ = val.policy_trade_returns(model, pp, te, label_col=label,
                                            cost_col=tc.cost_col,
                                            model_cost_col=tc.model_cost_col, cost_mult=mult)
            rs.append(r)
        allr = np.concatenate(rs) if rs else np.array([])
        e = float(allr.mean()) if allr.size else 0.0
        exps[mult] = e
        out[f"exp_{mult}"] = e
    out["sign_stable"] = val.cost_stress_sign_stable(exps)
    return out


def _shuffle_null(df, tc, folds, feature_cols) -> dict:
    """Re-run each fold on consistently permuted labels; AUC/mu_corr must collapse to null (F8).

    Labels are permuted ONCE over the whole frame so that if the pipeline leaks -- e.g.
    train and test share rows (contamination), or preprocessing sees the target -- the
    permuted-label run still scores above the no-information null and the run is INVALID.
    A clean pipeline with disjoint, purged folds scores ~0.5 AUC / ~0 corr."""
    rng = np.random.default_rng(tc.seed + 777)
    dfp = df.copy()
    specs = {s.col: s for s in discover_labels(df.columns)}
    for label in {f[3] for f in folds}:
        dfp[label] = dfp[label].to_numpy()[rng.permutation(len(dfp))]
    n_eff = effective_n(df, tc.symbol_col, tc.session_col)
    aucs, corrs = [], []
    n_pos = n_neg = 0
    for _model, _pp, _te, label, tr_s, te_s in folds:
        tr = val.rows_for_sessions(dfp, tr_s, tc.session_col)
        te = val.rows_for_sessions(dfp, te_s, tc.session_col)
        if tr.empty or te.empty or len(np.unique(np.sign(tr[label]))) < 2:
            continue
        try:
            m = _fit_model(tr, feature_cols, specs[label], n_eff, tc, "null", tc.seed)
        except ValueError:
            continue
        diag = val.fold_diagnostics(m, te, label)
        if np.isfinite(diag["auc"]):
            aucs.append(diag["auc"])
            n_pos += int(diag["n_pos"])
            n_neg += int(diag["n_neg"])
        if np.isfinite(diag["mu_corr"]):
            corrs.append(diag["mu_corr"])
    auc = float(np.mean(aucs)) if aucs else float("nan")
    mu_corr = float(np.mean(corrs)) if corrs else float("nan")
    tol = val.null_auc_tolerance(auc, n_pos, n_neg)
    return {"auc": auc, "mu_corr": mu_corr, "n_pos": n_pos, "n_neg": n_neg,
            "auc_tol": tol, "is_null": val.is_null_result(auc, mu_corr, n_pos, n_neg)}
