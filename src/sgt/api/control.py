"""Control-plane API (docs/IMPLEMENTATION_PLAN.md §14) — the authenticated operator
surface for arming/disarming trading, moving strategies along the capital ladder,
enabling/disabling accounts, recording manual fills, and reading the audit trail.

Security posture (fail closed, never open):
- EVERY endpoint requires a bearer token. The expected token comes from
  ``sgt.core.secrets.get_secret("control_plane_token")``. If no token is configured the
  whole surface returns 503 — it never falls open. A wrong/missing header returns 401.
  The token is compared with ``hmac.compare_digest`` (constant time) and is never logged.
- Request bodies are strict pydantic models (``extra="forbid"``, typed, length-bounded),
  so malformed or oversized input is rejected with 422 before any state changes.
- Every state-changing call writes a namespaced audit row (``RiskStore.append_audit``)
  carrying the acting operator, in addition to the audit the store methods emit.

The router is mounted at ``/control`` on the existing FastAPI app. The app must be bound
to a loopback host (``settings.control_plane_host``); see ``scripts/run_control_plane.py``.
"""
from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from sgt.accounts.ledger import enqueue_manual_fill
from sgt.accounts.models import Account
from sgt.brokers.alert_only import AlertOnlyBroker
from sgt.accounts.registry import enabled_accounts, set_enabled
from sgt.core.secrets import get_secret
from sgt.core.types import Side
from sgt.db.session import get_session
from sgt.risk.limits import LadderStage
from sgt.risk.models import StrategyStatus
from sgt.risk.state import RiskStore

CONTROL_TOKEN_SECRET = "control_plane_token"
# Namespaces the control plane manages (modes with durable risk state).
Namespace = Literal["live", "paper", "shadow"]
STATUS_NAMESPACES: tuple[str, ...] = ("live", "paper", "shadow")
_MAX_AUDIT_LIMIT = 500


# --------------------------------------------------------------------------- auth


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Fail-closed bearer-token auth applied to every control endpoint.

    No configured token -> 503 (never open). Missing/malformed/wrong token -> 401.
    The presented token is never logged; comparison is constant time.
    """
    configured = get_secret(CONTROL_TOKEN_SECRET, required=False)
    if not configured:
        raise HTTPException(status_code=503, detail="control plane token not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    presented = authorization[len("Bearer "):]
    # Compare bytes: str compare_digest raises TypeError on non-ASCII input (-> 500).
    if not hmac.compare_digest(presented.encode("utf-8"), configured.encode("utf-8")):
        raise HTTPException(status_code=401, detail="invalid token")


router = APIRouter(prefix="/control", tags=["control"], dependencies=[Depends(require_token)])


# --------------------------------------------------------------------------- request models


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArmRequest(_Strict):
    namespace: Namespace
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=512)
    confirm: str | None = Field(default=None, max_length=32)


class DisarmRequest(_Strict):
    namespace: Namespace
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="disarm", max_length=512)


class StageRequest(_Strict):
    namespace: Namespace
    stage: Literal["shadow", "micro", "small", "standard"]
    actor: str = Field(min_length=1, max_length=128)


class DisableStrategyRequest(_Strict):
    namespace: Namespace
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="manual_disable", max_length=512)


class AccountActionRequest(_Strict):
    namespace: Namespace = "live"
    actor: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=512)


class ManualFillRequest(_Strict):
    namespace: Namespace = "live"
    account_id: str = Field(min_length=1, max_length=64)
    client_order_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=16)
    side: Literal["buy", "sell"]
    qty: int = Field(gt=0, le=10_000_000)
    price: float = Field(gt=0.0, le=1_000_000.0)
    executed_at: datetime
    recorded_by: str = Field(min_length=1, max_length=64)

    @field_validator("executed_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("executed_at must be timezone-aware (UTC)")
        return v.astimezone(timezone.utc)


# --------------------------------------------------------------------------- helpers


def _store(namespace: str) -> RiskStore:
    return RiskStore(namespace)


def _switch_payload(namespace: str) -> dict:
    sw = _store(namespace).get_switch()
    return {
        "armed": sw.armed,
        "armed_by": sw.armed_by,
        "armed_at": sw.armed_at,
        "reason": sw.reason,
    }


def _strategies_payload(session, namespace: str) -> list[dict]:
    rows = session.scalars(
        select(StrategyStatus).where(StrategyStatus.namespace == namespace)
        .order_by(StrategyStatus.strategy_id)
    ).all()
    return [
        {
            "strategy_id": r.strategy_id,
            "enabled": r.enabled,
            "stage": r.stage,
            "size_multiplier": r.size_multiplier,
            "disabled_reason": r.disabled_reason,
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- endpoints


@router.get("/status")
def status() -> dict:
    """Switch state, registered strategies (per namespace) and enabled accounts."""
    with get_session() as session:
        namespaces = {
            ns: {
                "switch": _switch_payload(ns),
                "strategies": _strategies_payload(session, ns),
            }
            for ns in STATUS_NAMESPACES
        }
        accounts = [
            {
                "account_id": a.account_id,
                "broker": a.broker,
                "kind": a.kind,
                "allocation_weight": a.allocation_weight,
                "max_notional": a.max_notional,
            }
            for a in enabled_accounts(session)
        ]
    return {"namespaces": namespaces, "accounts": accounts}


@router.post("/arm")
def arm(req: ArmRequest) -> dict:
    """Arm the master switch for a namespace. Arming 'live' requires confirm='ARM LIVE'."""
    if req.namespace == "live" and req.confirm != "ARM LIVE":
        raise HTTPException(status_code=400, detail="arming live requires confirm='ARM LIVE'")
    store = _store(req.namespace)
    store.set_switch(armed=True, actor=req.actor, reason=req.reason)
    store.append_audit("control_arm", {
        "actor": req.actor, "reason": req.reason, "namespace": req.namespace})
    return {"namespace": req.namespace, "switch": _switch_payload(req.namespace)}


@router.post("/disarm")
def disarm(req: DisarmRequest) -> dict:
    """Disarm the master switch for a namespace. Always permitted (fail-safe)."""
    store = _store(req.namespace)
    store.set_switch(armed=False, actor=req.actor, reason=req.reason)
    store.append_audit("control_disarm", {
        "actor": req.actor, "reason": req.reason, "namespace": req.namespace})
    return {"namespace": req.namespace, "switch": _switch_payload(req.namespace)}


@router.post("/strategies/{strategy_id}/stage")
def set_stage(
    req: StageRequest,
    strategy_id: str = Path(min_length=1, max_length=64),
) -> dict:
    """Move a strategy to a capital-ladder stage (promotion requires an actor)."""
    stage = LadderStage(req.stage)
    store = _store(req.namespace)
    state = store.upsert_strategy(strategy_id, enabled=True, stage=stage)
    store.append_audit("control_stage", {
        "actor": req.actor, "strategy_id": strategy_id,
        "stage": stage.value, "namespace": req.namespace})
    return {
        "namespace": req.namespace, "strategy_id": state.strategy_id,
        "stage": state.stage.value, "enabled": state.enabled,
    }


@router.post("/strategies/{strategy_id}/disable")
def disable_strategy(
    req: DisableStrategyRequest,
    strategy_id: str = Path(min_length=1, max_length=64),
) -> dict:
    """Disable a strategy in a namespace (no new entries; exits still allowed)."""
    store = _store(req.namespace)
    state = store.upsert_strategy(strategy_id, enabled=False, disabled_reason=req.reason)
    store.append_audit("control_disable_strategy", {
        "actor": req.actor, "strategy_id": strategy_id,
        "reason": req.reason, "namespace": req.namespace})
    return {
        "namespace": req.namespace, "strategy_id": state.strategy_id,
        "enabled": state.enabled, "disabled_reason": state.disabled_reason,
    }


@router.post("/accounts/{account_id}/enable")
def enable_account(
    req: AccountActionRequest,
    account_id: str = Path(min_length=1, max_length=64),
) -> dict:
    return _set_account_enabled(account_id, True, req)


@router.post("/accounts/{account_id}/disable")
def disable_account(
    req: AccountActionRequest,
    account_id: str = Path(min_length=1, max_length=64),
) -> dict:
    return _set_account_enabled(account_id, False, req)


def _set_account_enabled(account_id: str, enabled: bool, req: AccountActionRequest) -> dict:
    with get_session() as session:
        try:
            set_enabled(session, account_id, enabled)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"unknown account {account_id!r}")
        _store(req.namespace).append_audit_in(
            session,
            "control_account_enabled" if enabled else "control_account_disabled",
            {"actor": req.actor, "account_id": account_id,
             "reason": req.reason, "namespace": req.namespace},
        )
    return {"account_id": account_id, "enabled": enabled}


@router.post("/manual-fills")
def record_manual_fill(req: ManualFillRequest) -> dict:
    """Queue an operator-reported execution into the alert-only manual-fill inbox."""
    with get_session() as session:
        account = session.get(Account, req.account_id)
        if account is None:
            raise HTTPException(status_code=404, detail=f"unknown account {req.account_id!r}")
        if account.broker != AlertOnlyBroker.name:
            # Only alert-only accounts drain the inbox; anything else would sit forever.
            raise HTTPException(status_code=409,
                                detail=f"account {req.account_id!r} is not an alert-only account")
        inbox_id = enqueue_manual_fill(
            session,
            account_id=req.account_id,
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            side=Side(req.side),
            qty=req.qty,
            price=req.price,
            executed_at=req.executed_at,
            recorded_by=req.recorded_by,
        )
        _store(req.namespace).append_audit_in(session, "control_manual_fill", {
            "recorded_by": req.recorded_by, "account_id": req.account_id,
            "client_order_id": req.client_order_id, "symbol": req.symbol,
            "side": req.side, "qty": req.qty, "price": req.price,
            "inbox_id": inbox_id, "namespace": req.namespace})
    return {"inbox_id": inbox_id, "account_id": req.account_id, "symbol": req.symbol}


@router.get("/audit")
def audit(
    namespace: Namespace = Query(default="live"),
    limit: int = Query(default=100, ge=1, le=_MAX_AUDIT_LIMIT),
) -> dict:
    """Most recent audit rows for a namespace (bounded to <=500)."""
    rows = _store(namespace).iter_audit()
    recent = rows[-limit:]
    return {
        "namespace": namespace,
        "count": len(recent),
        "audit": [{"ts": ts, "kind": kind, "detail": detail} for ts, kind, detail in recent],
    }
