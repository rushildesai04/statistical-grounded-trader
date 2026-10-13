"""Persistence for search trials and the strategy registry (plan §10 trial accounting).

All writes are idempotent on their natural key so a search can be re-run, resumed
after a crash, or run concurrently without inflating the trial count that the
Deflated Sharpe Ratio depends on. The DSR is only honest if ``n_trials`` equals the
true number of distinct configs evaluated — never a double count from a retry.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sgt.search.models import REGISTRY_STATUSES, LockboxOpening, StrategyRegistry, Trial

# Registry statuses whose evidence is a valid baseline for live decay monitoring.
_VALIDATED_STATUSES = ("validated", "shadow", "champion")


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class DecayBaseline:
    """Validated out-of-sample baseline the live decay monitors compare against
    (plan §11.4). ``source`` records where it came from (``registry`` or ``backtest``)."""

    expectancy: float          # per-trade expectancy (return units)
    ret_std: float             # std of per-trade returns (> 0)
    win_rate: float            # fraction of winning trades, in (0, 1)
    max_dd_budget: float | None = None     # dollar drawdown budget, if known
    modeled_slippage: float | None = None  # modeled per-trade slippage reference
    source: str = "registry"


def _pick(d: dict, *keys: str):
    for k in keys:
        v = d.get(k)
        if v is not None:
            return v
    return None


def get_validated_baseline(session: Session, config_hash: str) -> DecayBaseline | None:
    """Return the validated decay baseline for ``config_hash`` from the registry/lockbox,
    or ``None`` if the config is not validated or its evidence lacks the required stats
    (expectancy, per-trade return std, win rate). The live runner treats ``None`` as
    fail-closed for LIVE mode.

    Evidence may carry a dedicated ``decay_baseline`` block or the acceptance ``lockbox``
    report plus sibling ``ret_std``/``win_rate`` keys; both are accepted."""
    merged: dict = {}
    row = session.get(StrategyRegistry, config_hash)
    if row is not None and row.status in _VALIDATED_STATUSES and isinstance(row.evidence, dict):
        merged.update(row.evidence)
    opening = lockbox_opening(session, config_hash)
    if opening is not None and isinstance(opening.result, dict):
        for k, v in opening.result.items():
            merged.setdefault(k, v)
    if not merged:
        return None

    block = merged.get("decay_baseline") if isinstance(merged.get("decay_baseline"), dict) else {}
    lockbox = merged.get("lockbox") if isinstance(merged.get("lockbox"), dict) else {}
    exp = _pick(block, "expectancy") or _pick(lockbox, "expectancy") or merged.get("expectancy")
    std = _pick(block, "ret_std", "return_std", "std") or _pick(merged, "ret_std", "return_std")
    wr = _pick(block, "win_rate") or merged.get("win_rate")
    if exp is None or std is None or wr is None:
        return None
    try:
        exp, std, wr = float(exp), float(std), float(wr)
    except (TypeError, ValueError):
        return None
    if std <= 0 or not (0.0 < wr < 1.0):
        return None
    dd = _pick(block, "max_dd_budget") or merged.get("max_dd_budget")
    slip = _pick(block, "modeled_slippage") or merged.get("modeled_slippage")
    return DecayBaseline(
        expectancy=exp, ret_std=std, win_rate=wr,
        max_dd_budget=float(dd) if dd is not None else None,
        modeled_slippage=float(slip) if slip is not None else None, source="registry")


def record_trial(
    session: Session,
    *,
    search_run_id: str,
    config_hash: str,
    params: dict,
    metrics: dict,
    n_trades: int,
) -> bool:
    """Insert one trial. Returns True if inserted, False if (run, config) already
    recorded (idempotent re-run). Never updates an existing row."""
    existing = session.execute(
        select(Trial.id).where(
            Trial.search_run_id == search_run_id, Trial.config_hash == config_hash
        )
    ).first()
    if existing is not None:
        return False
    session.add(Trial(
        search_run_id=search_run_id, config_hash=config_hash, params=params,
        metrics=metrics, n_trades=int(n_trades), created_at=_now(),
    ))
    session.flush()
    return True


def trial_count(session: Session, search_run_id: str) -> int:
    """Number of distinct configs recorded for a search run (the DSR trial count)."""
    return int(session.execute(
        select(func.count()).select_from(Trial).where(Trial.search_run_id == search_run_id)
    ).scalar_one())


def sharpe_variance_across_trials(session: Session, search_run_id: str) -> float | None:
    """Sample variance of the per-trial per-trade Sharpe estimates for a run.

    This is the cross-trial Sharpe dispersion the DSR deflates against. Returns None
    when fewer than two trials carry a finite ``sharpe`` metric (nothing to deflate)."""
    rows = session.execute(
        select(Trial.metrics).where(Trial.search_run_id == search_run_id)
    ).scalars().all()
    sharpes = [
        float(m["sharpe"]) for m in rows
        if isinstance(m, dict) and m.get("sharpe") is not None and math.isfinite(float(m["sharpe"]))
    ]
    if len(sharpes) < 2:
        return None
    mean = sum(sharpes) / len(sharpes)
    var = sum((s - mean) ** 2 for s in sharpes) / (len(sharpes) - 1)
    return float(var)


def upsert_registry(
    session: Session,
    *,
    config_hash: str,
    config: dict,
    status: str = "candidate",
    evidence: dict | None = None,
) -> StrategyRegistry:
    """Create or update the registry row for a config hash (its versioned identity)."""
    if status not in REGISTRY_STATUSES:
        raise ValueError(f"status must be one of {REGISTRY_STATUSES}, got {status!r}")
    row = session.get(StrategyRegistry, config_hash)
    if row is None:
        row = StrategyRegistry(
            config_hash=config_hash, config=config, created_at=_now(),
            status=status, evidence=evidence or {})
        session.add(row)
    else:
        row.status = status
        if evidence is not None:
            row.evidence = evidence
    session.flush()
    return row


def lockbox_opening(session: Session, config_hash: str) -> LockboxOpening | None:
    return session.execute(
        select(LockboxOpening).where(LockboxOpening.config_hash == config_hash)
    ).scalar_one_or_none()
