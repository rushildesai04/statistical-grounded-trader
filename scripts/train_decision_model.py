"""Train and validate the decision model from a dataset-contract parquet.

Reads the dataset parquet built by the dataset workstream (one row per candidate, see
DECISION_MODEL.md "Dataset contract"), runs nested session-grouped CPCV training with
honest trial accounting into the persistent ledger, and writes the fitted artifact plus a
markdown report. Sessions on/after ``--lockbox-start`` are held out of training entirely
(the one-time lockbox engine run happens separately via sgt.search.lockbox).

Usage:
    python scripts/train_decision_model.py --dataset data/decision/<id>/dataset.parquet \
        --lockbox-start 2026-10-01 --out data/decision/<id>/model
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from sgt.db.session import get_session, init_db
from sgt.decision.report import render_report
from sgt.decision import trial_ledger
from sgt.decision.lockbox import evaluate_lockbox
from sgt.decision.train import TrainConfig, discover_labels, train_decision_model

log = logging.getLogger("train_decision_model")


def _parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Train the decision model from a dataset parquet.")
    ap.add_argument("--dataset", required=True, type=Path, help="dataset.parquet path")
    ap.add_argument("--lockbox-start", required=True, type=date.fromisoformat,
                    help="first held-out session (YYYY-MM-DD); not used for training")
    ap.add_argument("--out", required=True, type=Path,
                    help="output path prefix for the model artifact + report")
    ap.add_argument("--run-id", default="decision-run", help="trial-ledger run id")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--max-per-cluster", type=int, default=0,
                    help="cap candidates per (symbol, session) with a seeded sample; signals "
                         "within one symbol-day are near-duplicates, so this shrinks compute "
                         "without removing any independent cluster (0 = keep all)")
    ap.add_argument("--jobs", type=int, default=1,
                    help="worker processes for inner model selection (results identical)")
    ap.add_argument("--dataset-cost", choices=("model", "calibrated"), default=None,
                    help="cost source of a dataset built before meta.json recorded a "
                         "cost_spec; verified against the f_cost_rt_pct feature")
    ap.add_argument("--equity", type=float, default=100_000.0,
                    help="account size for the lockbox dollar simulation")
    return ap.parse_args(argv)


def _horizon_minutes(h: int, meta: dict) -> int:
    """Label horizon in minutes for the lockbox one-position-per-symbol rule. Swing
    datasets count H in trading days: 5 trading days span 7 calendar days."""
    if meta.get("label_unit") == "days":
        return int(h * 1440 * 7 / 5)
    return int(h)


def _resolve_cost_spec(meta: dict, override: str | None, df: pd.DataFrame) -> dict:
    """The dataset's cost spec (F-2). Legacy datasets need ``--dataset-cost``, which is
    accepted only if it reproduces the dataset's own f_cost_rt_pct feature."""
    from sgt.brokers.costs import DEFAULT_COST_MODEL
    from sgt.decision.cost_spec import check_bucket_spec_matches, spec_of
    from sgt.decision.features import CostModelAdapter

    if meta.get("cost_spec"):
        return meta["cost_spec"]
    if override is None:
        raise SystemExit("dataset meta.json has no cost_spec; pass --dataset-cost")
    if override == "calibrated":
        from sgt.brokers.costs import CALIBRATED_COST_MODEL
        spec = spec_of(CostModelAdapter(CALIBRATED_COST_MODEL, tag="calibrated"))
    else:
        spec = spec_of(CostModelAdapter(DEFAULT_COST_MODEL))
    match = check_bucket_spec_matches(df, spec)
    log.info("--dataset-cost %s reproduces f_cost_rt_pct on %.2f%% of sampled rows",
             override, 100 * match)
    if not match >= 0.99:
        raise SystemExit(f"--dataset-cost {override} does not match the dataset's costs "
                         f"({100 * match:.1f}% of rows)")
    return spec


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    if not args.dataset.exists():
        raise SystemExit(f"dataset not found: {args.dataset}")

    df = pd.read_parquet(args.dataset)
    # F-1 (2026-10-18): the hurdle / cost stress must use the cost the LABELS were netted
    # with. New datasets carry it as cost_rt_label; older ones only have it as the
    # f_cost_rt_pct feature (same cost source, priced at the signal bar, in percent).
    if "cost_rt_label" not in df.columns:
        if "f_cost_rt_pct" not in df.columns:
            raise SystemExit("dataset has neither cost_rt_label nor f_cost_rt_pct")
        log.warning("dataset predates cost_rt_label; deriving it from f_cost_rt_pct / 100")
        df["cost_rt_label"] = df["f_cost_rt_pct"] / 100.0
    tc = TrainConfig(run_id=args.run_id, seed=args.seed, n_jobs=max(1, args.jobs),
                     model_cost_col="cost_rt_label")
    session_col = tc.session_col
    if session_col not in df.columns:
        raise SystemExit(f"dataset missing {session_col!r} column")

    lockbox = args.lockbox_start.isoformat()
    # The lockbox is scored on ALL its candidates (what a live runner would see), never
    # the per-cluster training cap.
    held_full = df[df[session_col].astype(str) >= lockbox].copy()
    if args.max_per_cluster > 0:
        before = len(df)
        # Seeded shuffle, then keep the first N rows of each (symbol, session) cluster.
        df = (df.sample(frac=1.0, random_state=args.seed)
                .groupby(["symbol", session_col], sort=False).head(args.max_per_cluster)
                .sort_values(["ts", "symbol"]).reset_index(drop=True))
        log.info("capped to %d per (symbol, session): %d -> %d rows", args.max_per_cluster, before, len(df))

    train_df = df[df[session_col].astype(str) < lockbox].copy()
    meta_path = args.dataset.parent / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if meta.get("label_unit") == "days":
        # Multi-day labels overlap across sessions: the CV embargo must cover the longest
        # horizon, and training rows whose label window reaches into the lockbox are
        # purged (their outcomes are lockbox prices).
        max_h = max(s.H for s in discover_labels(df.columns))
        tc = dataclasses.replace(tc, embargo=max_h + 1)
        sess = sorted(train_df[session_col].astype(str).unique())
        purge_from = sess[-max_h] if len(sess) > max_h else lockbox
        before = len(train_df)
        train_df = train_df[train_df[session_col].astype(str) < purge_from].copy()
        log.info("day-unit labels: embargo=%d sessions; purged %d rows on/after %s",
                 tc.embargo, before - len(train_df), purge_from)
    held = int(len(df) - len(train_df))
    log.info("loaded %d rows; %d training, %d held out on/after %s",
             len(df), len(train_df), held, lockbox)
    if train_df.empty:
        raise SystemExit("no training rows before the lockbox start")

    init_db()
    with get_session() as session:
        result = train_decision_model(train_df, tc, session=session)

    # A momentum-biased research dataset can generate hypotheses but never promote
    # (2026-10-17 ruling; DECISION_MODEL.md §10 F2).
    if not meta.get("promotion_eligible", False) and result.verdict == "PROMOTE-CANDIDATE":
        result = dataclasses.replace(result, verdict="RESEARCH-PROMISING (dataset not promotion-eligible)")
    elif not meta.get("promotion_eligible", False):
        log.info("dataset is not promotion-eligible (research/ungated or missing meta.json)")

    cost_spec = _resolve_cost_spec(meta, args.dataset_cost, df)

    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    report_md = render_report(result)
    lb = None
    if result.model is not None and result.chosen_params is not None and not held_full.empty:
        spec = next(s for s in discover_labels(df.columns) if s.col == result.chosen_label)
        lb = evaluate_lockbox(result.model, result.chosen_params, held_full,
                              label_col=spec.col, horizon_min=_horizon_minutes(spec.H, meta),
                              cost_col=tc.cost_col,
                              model_cost_col=tc.model_cost_col, equity=args.equity,
                              session_col=session_col)
        report_md += "\n" + lb.to_markdown()
        if lb.trades is not None and not lb.trades.empty:
            lb.trades.to_csv(out.with_suffix(".lockbox_trades.csv"), index=False)
        with get_session() as session:
            # Every lockbox look is part of the lineage (no silent re-peeking).
            trial_ledger.record_trial(
                session, run_id=args.run_id, kind="lockbox",
                params={"lockbox_start": lockbox, "label": spec.col,
                        "z": result.chosen_params.z, "p_min": result.chosen_params.p_min},
                metrics={"n_trades": lb.n_trades, "mean": lb.mean_net_return,
                         "pnl_usd": lb.pnl_usd})
        log.info("lockbox: %d trades, mean %.4f%%, P&L $%.2f on $%.0f",
                 lb.n_trades, 100 * lb.mean_net_return, lb.pnl_usd, args.equity)
    out.with_suffix(".report.md").write_text(report_md)
    log.info("verdict=%s tier=%s n_eff=%d dsr=%.3f -> %s",
             result.verdict, result.tier, result.n_eff, result.deflated_sharpe,
             out.with_suffix(".report.md"))

    if result.model is not None:
        # F1 (DECISION_MODEL.md §10): stamp the verdict + dataset eligibility + run id into
        # the artifact metadata, then quarantine any artifact that is not fit for trading so
        # the load path refuses it. A quarantined file is still written for research.
        promotion_eligible = bool(meta.get("promotion_eligible", False))
        # F-2: every runtime rebuilds the cost source the model was trained against.
        result.model.metadata["cost_spec"] = cost_spec
        result.model.set_release_metadata(
            verdict=result.verdict, promotion_eligible=promotion_eligible,
            run_id=args.run_id)
        eligible = result.model.is_promotion_eligible()
        suffix = ".joblib" if eligible else ".quarantine.joblib"
        artifact = result.model.save(out.with_suffix(suffix))
        if eligible:
            log.info("wrote promotion-eligible model artifact %s", artifact)
        else:
            log.warning(
                "QUARANTINED model artifact %s (verdict=%s, promotion_eligible=%s): "
                "refused by the trading load path, kept for research only",
                artifact, result.verdict, promotion_eligible)
    else:
        log.warning("no model artifact written (no configuration selected)")
    print(report_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
