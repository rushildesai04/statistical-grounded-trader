"""Account registry: load enabled accounts and their limits, with validation.

The router and reconciler operate on immutable ``AccountSpec`` snapshots, never on
live ORM rows, so a trading decision is reproducible from the snapshot it was made on.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from sgt.accounts.models import Account

_VALID_KINDS = {"cash", "margin", "ira"}


@dataclass(frozen=True, slots=True)
class AccountSpec:
    """Immutable view of one account used for allocation and reconciliation."""

    account_id: str
    broker: str
    kind: str
    enabled: bool
    allocation_weight: float
    max_notional: float

    def __post_init__(self) -> None:
        if not self.account_id:
            raise ValueError("account_id required")
        if self.kind not in _VALID_KINDS:
            raise ValueError(f"kind must be one of {_VALID_KINDS}, got {self.kind!r}")
        if self.allocation_weight <= 0:
            raise ValueError("allocation_weight must be > 0")
        if self.max_notional < 0:
            raise ValueError("max_notional must be >= 0")

    @property
    def is_cash(self) -> bool:
        return self.kind == "cash"


def _to_spec(row: Account) -> AccountSpec:
    return AccountSpec(
        account_id=row.account_id,
        broker=row.broker,
        kind=row.kind,
        enabled=row.enabled,
        allocation_weight=row.allocation_weight,
        max_notional=row.max_notional,
    )


def upsert_account(
    session: Session,
    *,
    account_id: str,
    broker: str,
    kind: str = "cash",
    enabled: bool = True,
    allocation_weight: float = 1.0,
    max_notional: float = 0.0,
) -> AccountSpec:
    """Create or update an account. Validates via AccountSpec before persisting."""
    spec = AccountSpec(account_id, broker, kind, enabled, allocation_weight, max_notional)
    row = session.get(Account, account_id)
    if row is None:
        row = Account(account_id=account_id, created_at=datetime.now(timezone.utc))
        session.add(row)
    row.broker = broker
    row.kind = kind
    row.enabled = enabled
    row.allocation_weight = allocation_weight
    row.max_notional = max_notional
    session.flush()
    return spec


def set_enabled(session: Session, account_id: str, enabled: bool) -> None:
    row = session.get(Account, account_id)
    if row is None:
        raise KeyError(f"unknown account {account_id!r}")
    row.enabled = enabled
    session.flush()


def enabled_accounts(session: Session) -> list[AccountSpec]:
    rows = session.scalars(
        select(Account).where(Account.enabled.is_(True)).order_by(Account.account_id)
    ).all()
    return [_to_spec(r) for r in rows]


def all_accounts(session: Session) -> list[AccountSpec]:
    rows = session.scalars(select(Account).order_by(Account.account_id)).all()
    return [_to_spec(r) for r in rows]
