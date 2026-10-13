"""Lockbox: the one-time, logged evaluation of a finalist on held-out data (plan §10, §11.3).

The most recent sessions (``>= lockbox_start``) are never touched by search. A finalist
is evaluated against them EXACTLY ONCE. ``open_lockbox`` runs the config on the lockbox
sessions (with pre-lockbox warm-up, counting only lockbox-session entries), scores it
with :func:`sgt.learning.validation.evaluate_acceptance` using the true trial count and
cross-trial Sharpe variance from the ``trials`` table, and records the opening. A second
attempt on the same config raises :class:`LockboxAlreadyOpened` — re-opening would turn
the held-out set into just another thing we optimised against.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Mapping, Sequence

from sqlalchemy.exc import IntegrityError

from sgt.core.types import Bar
from sgt.db.session import get_session
from sgt.learning.validation import AcceptanceReport, evaluate_acceptance
from sgt.search import registry
from sgt.search.models import LockboxOpening
from sgt.search.runner import evaluate_oos
from sgt.strategy.loader import config_hash as compute_config_hash
from sgt.strategy.schema import StrategyConfig


class LockboxAlreadyOpened(RuntimeError):
    """Raised when a config's lockbox has already been opened (one opening per config)."""


def open_lockbox(
    config: StrategyConfig,
    bars_by_symbol: Mapping[str, Sequence[Bar]],
    lockbox_start: date,
    *,
    search_run_id: str,
    starting_cash: float = 100_000.0,
    seed: int = 0,
    max_dd_budget: float | None = None,
    dsr_threshold: float = 0.95,
    reference: object = None,
    allow_unknown_float: bool = False,
) -> AcceptanceReport:
    """Evaluate ``config`` once on the lockbox sessions and persist the opening.

    ``n_trials`` and the cross-trial Sharpe variance are read from the ``trials`` table
    for ``search_run_id`` so the Deflated Sharpe Ratio reflects the whole search lineage.
    """
    cfg_hash = compute_config_hash(config)

    all_dates = sorted({b.session_date for bars in bars_by_symbol.values() for b in bars})
    test = tuple(d for d in all_dates if d >= lockbox_start)
    warmup = tuple(d for d in all_dates if d < lockbox_start)
    if not test:
        raise ValueError(f"no lockbox sessions on/after {lockbox_start}")

    with get_session() as session:
        if registry.lockbox_opening(session, cfg_hash) is not None:
            raise LockboxAlreadyOpened(f"lockbox already opened for {cfg_hash}")

        oos = evaluate_oos(
            config, bars_by_symbol, [(warmup, test)], starting_cash=starting_cash,
            run_tag=f"lockbox:{cfg_hash}", reference=reference,
            allow_unknown_float=allow_unknown_float)
        if oos.returns.size < 2:
            raise ValueError(
                f"lockbox produced {oos.returns.size} trades; need >= 2 to evaluate acceptance")

        n_trials = max(1, registry.trial_count(session, search_run_id))
        sr_var = registry.sharpe_variance_across_trials(session, search_run_id)
        report = evaluate_acceptance(
            list(oos.returns), n_trials, sr_variance_across_trials=sr_var,
            dsr_threshold=dsr_threshold, max_dd_budget=max_dd_budget, seed=seed)

        evidence = {
            "lockbox": report.as_dict(),
            "n_trials": n_trials,
            "sr_variance_across_trials": sr_var,
            "lockbox_start": lockbox_start.isoformat(),
        }
        session.add(LockboxOpening(
            config_hash=cfg_hash, search_run_id=search_run_id,
            opened_at=datetime.now(timezone.utc), result=evidence))
        try:
            session.flush()
        except IntegrityError as e:  # concurrent opener won the race
            session.rollback()
            raise LockboxAlreadyOpened(f"lockbox already opened for {cfg_hash}") from e

        registry.upsert_registry(
            session, config_hash=cfg_hash, config=config.model_dump(mode="json"),
            status="validated" if report.accepted else "candidate", evidence=evidence)

    return report
