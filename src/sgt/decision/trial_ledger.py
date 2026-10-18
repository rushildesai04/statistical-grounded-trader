"""Persistent trial ledger for the decision model (DECISION_MODEL.md §10 F5).

Every configuration ever evaluated on the same data is recorded so the Deflated Sharpe
Ratio deflates against the TRUE number of trials, not just the ones in the current run.
This includes the prior 2026-10-17 learning-pass trials, seeded here as already spent:
64 bracket combinations + 6 filter fits + 288 search-grid points = 358 trials.

Writes are idempotent on ``(run_id, config_hash)`` so re-running a search, resuming after
a crash, or seeding priors twice never inflates the count. The table is registered on the
shared ``Base`` by a single import line in ``sgt.db.registry``.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, UniqueConstraint, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from sgt.db.models import Base

PRIOR_RUN_ID = "learning-pass-2026-10-17"
PRIOR_BRACKET_TRIALS = 64
PRIOR_FILTER_TRIALS = 6
PRIOR_SEARCH_TRIALS = 288
PRIOR_TOTAL = PRIOR_BRACKET_TRIALS + PRIOR_FILTER_TRIALS + PRIOR_SEARCH_TRIALS


class DecisionTrial(Base):
    __tablename__ = "decision_trials"
    __table_args__ = (
        UniqueConstraint("run_id", "config_hash", name="uq_decision_trials_run_config"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(80), index=True)
    config_hash: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(24), default="decision", index=True)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def config_hash(params: dict) -> str:
    """Stable 32-hex hash of a config dict (its identity in the ledger)."""
    blob = json.dumps(params, sort_keys=True, default=str).encode()
    return hashlib.sha256(blob).hexdigest()[:32]


def record_trial(
    session: Session,
    *,
    run_id: str,
    params: dict,
    metrics: dict,
    kind: str = "decision",
    cfg_hash: str | None = None,
) -> bool:
    """Insert one trial. Returns True if inserted, False if already recorded (idempotent)."""
    ch = cfg_hash or config_hash(params)
    existing = session.execute(
        select(DecisionTrial.id).where(
            DecisionTrial.run_id == run_id, DecisionTrial.config_hash == ch)
    ).first()
    if existing is not None:
        return False
    session.add(DecisionTrial(
        run_id=run_id, config_hash=ch, kind=kind, params=params, metrics=metrics,
        created_at=_now()))
    # Commit immediately (2026-10-18, DB-2): a trainer holding one transaction for hours
    # locked the shared SQLite DB for every other writer, and a crash would have rolled
    # back the trial count that the deflated Sharpe ratio depends on.
    try:
        session.commit()
    except IntegrityError:
        # F4 (2026-10-18): the select-then-insert above is not atomic. A concurrent run
        # may insert the same (run_id, config_hash) between our SELECT and COMMIT; the
        # unique constraint then fires. Treat it as already-recorded (idempotent) rather
        # than crashing the losing writer.
        session.rollback()
        return False
    return True


def trial_count(session: Session, *run_ids: str) -> int:
    """Distinct configs recorded across the given run ids (the DSR trial count)."""
    stmt = select(func.count()).select_from(DecisionTrial)
    if run_ids:
        stmt = stmt.where(DecisionTrial.run_id.in_(run_ids))
    return int(session.execute(stmt).scalar_one())


def sharpe_variance_across_trials(session: Session, *run_ids: str) -> float | None:
    """Sample variance of per-trial Sharpe estimates (the DSR deflation dispersion)."""
    stmt = select(DecisionTrial.metrics)
    if run_ids:
        stmt = stmt.where(DecisionTrial.run_id.in_(run_ids))
    rows = session.execute(stmt).scalars().all()
    sharpes = [
        float(m["sharpe"]) for m in rows
        if isinstance(m, dict) and m.get("sharpe") is not None
        and math.isfinite(float(m["sharpe"]))
    ]
    if len(sharpes) < 2:
        return None
    mean = sum(sharpes) / len(sharpes)
    return float(sum((s - mean) ** 2 for s in sharpes) / (len(sharpes) - 1))


def seed_prior_trials(session: Session) -> int:
    """Seed the 2026-10-17 learning-pass trials as spent. Idempotent; returns inserted count.

    These configs were already evaluated on the same data, so they must count toward the
    DSR trial total even though their per-trial Sharpe is unknown (no ``sharpe`` metric
    -> they inflate ``n_trials`` without distorting the cross-trial variance)."""
    inserted = 0
    plan = [("bracket", PRIOR_BRACKET_TRIALS), ("filter", PRIOR_FILTER_TRIALS),
            ("search", PRIOR_SEARCH_TRIALS)]
    for kind, count in plan:
        for i in range(count):
            if record_trial(
                session, run_id=PRIOR_RUN_ID,
                params={"prior_kind": kind, "index": i}, metrics={},
                kind=kind, cfg_hash=config_hash({"prior": kind, "i": i})):
                inserted += 1
    return inserted
