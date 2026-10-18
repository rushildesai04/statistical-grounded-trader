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
from sgt.decision.train import TrainConfig, train_decision_model

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
    return ap.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    if not args.dataset.exists():
        raise SystemExit(f"dataset not found: {args.dataset}")

    df = pd.read_parquet(args.dataset)
    tc = TrainConfig(run_id=args.run_id, seed=args.seed)
    session_col = tc.session_col
    if session_col not in df.columns:
        raise SystemExit(f"dataset missing {session_col!r} column")

    if args.max_per_cluster > 0:
        before = len(df)
        # Seeded shuffle, then keep the first N rows of each (symbol, session) cluster.
        df = (df.sample(frac=1.0, random_state=args.seed)
                .groupby(["symbol", session_col], sort=False).head(args.max_per_cluster)
                .sort_values(["ts", "symbol"]).reset_index(drop=True))
        log.info("capped to %d per (symbol, session): %d -> %d rows", args.max_per_cluster, before, len(df))

    lockbox = args.lockbox_start.isoformat()
    train_df = df[df[session_col].astype(str) < lockbox].copy()
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
    meta_path = args.dataset.parent / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if not meta.get("promotion_eligible", False) and result.verdict == "PROMOTE-CANDIDATE":
        result = dataclasses.replace(result, verdict="RESEARCH-PROMISING (dataset not promotion-eligible)")
    elif not meta.get("promotion_eligible", False):
        log.info("dataset is not promotion-eligible (research/ungated or missing meta.json)")

    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    report_md = render_report(result)
    out.with_suffix(".report.md").write_text(report_md)
    log.info("verdict=%s tier=%s n_eff=%d dsr=%.3f -> %s",
             result.verdict, result.tier, result.n_eff, result.deflated_sharpe,
             out.with_suffix(".report.md"))

    if result.model is not None:
        # F1 (DECISION_MODEL.md §10): stamp the verdict + dataset eligibility + run id into
        # the artifact metadata, then quarantine any artifact that is not fit for trading so
        # the load path refuses it. A quarantined file is still written for research.
        promotion_eligible = bool(meta.get("promotion_eligible", False))
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
