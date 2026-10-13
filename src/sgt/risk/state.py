"""Persistent risk state access (RiskStore) and an in-memory equivalent.

Both stores expose the same interface (``RiskStoreLike``). Each store instance is bound
to one ``namespace`` so the engine's modes never share state (F-1): LIVE/PAPER/SHADOW
and each BACKTEST run get independent switches, strategy status, fill ledgers and audit
trails. A backtest/shadow/paper loss can therefore never consume the real-money learning
budget or corrupt live control state.

- ``RiskStore`` persists to the shared SQLAlchemy DB; use it for live/paper/shadow.
- ``InMemoryRiskStore`` holds state in process memory with identical semantics; use it
  for backtests and parameter search where thousands of runs must not touch SQLite.

Design points:
- On process start the switch is forced DISARMED (``load(fresh_start=True)``) and an
  audit row records it. Live trading never resumes armed after a restart.
- ``append_loss`` is idempotent on ``fill_id`` within the namespace and returns False if
  the fill was already recorded, so a replayed fill is never double-counted.
- The ledger is replayed by the manager on construction to rebuild positions and budget
  totals; see ``iter_ledger``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator, Mapping, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from sgt.db.session import get_session
from sgt.risk.limits import LadderStage
from sgt.risk.models import LossLedger, RiskAudit, RiskSwitch, StrategyStatus


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class SwitchState:
    armed: bool
    armed_by: str
    armed_at: datetime | None
    reason: str


@dataclass(frozen=True, slots=True)
class StrategyState:
    strategy_id: str
    enabled: bool
    stage: LadderStage
    size_multiplier: float
    disabled_reason: str


@dataclass(frozen=True, slots=True)
class LedgerRow:
    ts: datetime
    account_id: str
    strategy_id: str
    symbol: str
    side: str
    qty: int
    price: float
    fee: float
    realized_pnl: float
    fill_id: str


@runtime_checkable
class RiskStoreLike(Protocol):
    """The store interface the RiskManager depends on (DB-backed or in-memory)."""

    namespace: str

    def load(self, *, fresh_start: bool = True) -> SwitchState: ...
    def get_switch(self) -> SwitchState: ...
    def set_switch(self, *, armed: bool, actor: str, reason: str) -> None: ...
    def get_strategy(self, strategy_id: str) -> StrategyState | None: ...
    def upsert_strategy(self, strategy_id: str, *, enabled: bool | None = None,
                        stage: LadderStage | None = None, size_multiplier: float | None = None,
                        disabled_reason: str | None = None) -> StrategyState: ...
    def has_fill(self, fill_id: str) -> bool: ...
    def append_loss(self, *, ts: datetime, account_id: str, strategy_id: str, symbol: str,
                    side: str, qty: int, price: float, fee: float, realized_pnl: float,
                    fill_id: str) -> bool: ...
    def iter_ledger(self) -> Iterator[LedgerRow]: ...
    def append_audit(self, kind: str, detail: Mapping[str, Any], ts: datetime | None = None) -> None: ...
    def iter_audit(self, kind: str | None = None) -> list[tuple[datetime, str, dict]]: ...


class RiskStore:
    """Durable risk state for one ``namespace``. All methods open a short transaction."""

    def __init__(self, namespace: str = "live") -> None:
        if not namespace:
            raise ValueError("RiskStore requires a non-empty namespace")
        self.namespace = namespace

    # ----------------------------------------------------------------- lifecycle
    def load(self, *, fresh_start: bool = True) -> SwitchState:
        """Ensure the namespace's switch row exists; force DISARMED on a fresh start.

        ``fresh_start=True`` (default) models process boot: the master switch is set to
        disarmed and the action audited, so a crash/restart never leaves the system
        armed. Pass ``fresh_start=False`` only to read current state without resetting.
        """
        with get_session() as s:
            row = s.get(RiskSwitch, self.namespace)
            now = _utcnow()
            if row is None:
                row = RiskSwitch(namespace=self.namespace, armed=False, armed_by="system",
                                 armed_at=None, reason="initialized", updated_at=now)
                s.add(row)
                s.add(self._audit_row(now, "switch_initialized", {"armed": False}))
            elif fresh_start and row.armed:
                row.armed = False
                row.armed_by = "system"
                row.reason = "forced disarmed on process start"
                row.updated_at = now
                s.add(self._audit_row(now, "switch_forced_disarmed_on_restart", {}))
            elif fresh_start:
                s.add(self._audit_row(now, "switch_boot_disarmed", {}))
            return SwitchState(row.armed, row.armed_by, row.armed_at, row.reason)

    # ----------------------------------------------------------------- switch
    def get_switch(self) -> SwitchState:
        with get_session() as s:
            row = s.get(RiskSwitch, self.namespace)
            if row is None:
                return SwitchState(False, "", None, "")
            return SwitchState(row.armed, row.armed_by, row.armed_at, row.reason)

    def set_switch(self, *, armed: bool, actor: str, reason: str) -> None:
        now = _utcnow()
        with get_session() as s:
            row = s.get(RiskSwitch, self.namespace)
            if row is None:
                row = RiskSwitch(namespace=self.namespace)
                s.add(row)
            row.armed = armed
            row.armed_by = actor
            row.armed_at = now if armed else None
            row.reason = reason
            row.updated_at = now
            s.add(self._audit_row(now, "switch_armed" if armed else "switch_disarmed",
                                  {"actor": actor, "reason": reason}))

    # ----------------------------------------------------------------- strategy
    def get_strategy(self, strategy_id: str) -> StrategyState | None:
        with get_session() as s:
            row = s.get(StrategyStatus, (self.namespace, strategy_id))
            if row is None:
                return None
            return StrategyState(row.strategy_id, row.enabled, LadderStage(row.stage),
                                 row.size_multiplier, row.disabled_reason)

    def upsert_strategy(self, strategy_id: str, *, enabled: bool | None = None,
                        stage: LadderStage | None = None, size_multiplier: float | None = None,
                        disabled_reason: str | None = None) -> StrategyState:
        now = _utcnow()
        with get_session() as s:
            row = s.get(StrategyStatus, (self.namespace, strategy_id))
            if row is None:
                row = StrategyStatus(namespace=self.namespace, strategy_id=strategy_id, enabled=True,
                                     stage=LadderStage.SHADOW.value, size_multiplier=1.0,
                                     disabled_reason="", updated_at=now)
                s.add(row)
            if enabled is not None:
                row.enabled = enabled
            if stage is not None:
                row.stage = stage.value
            if size_multiplier is not None:
                row.size_multiplier = size_multiplier
            if disabled_reason is not None:
                row.disabled_reason = disabled_reason
            row.updated_at = now
            return StrategyState(row.strategy_id, row.enabled, LadderStage(row.stage),
                                 row.size_multiplier, row.disabled_reason)

    # ----------------------------------------------------------------- ledger
    def has_fill(self, fill_id: str) -> bool:
        with get_session() as s:
            return s.scalar(select(LossLedger.id).where(
                LossLedger.namespace == self.namespace, LossLedger.fill_id == fill_id)) is not None

    def append_loss(self, *, ts: datetime, account_id: str, strategy_id: str, symbol: str,
                    side: str, qty: int, price: float, fee: float, realized_pnl: float,
                    fill_id: str) -> bool:
        """Append one fill row. Returns False if ``fill_id`` was already recorded."""
        try:
            with get_session() as s:
                s.add(LossLedger(namespace=self.namespace, ts=ts, account_id=account_id,
                                 strategy_id=strategy_id, symbol=symbol, side=side, qty=qty,
                                 price=price, fee=fee, realized_pnl=realized_pnl, fill_id=fill_id))
        except IntegrityError:
            return False
        return True

    def iter_ledger(self) -> Iterator[LedgerRow]:
        """All ledger rows for this namespace in insertion order (for replay)."""
        with get_session() as s:
            stmt = select(LossLedger).where(LossLedger.namespace == self.namespace).order_by(LossLedger.id)
            for r in s.scalars(stmt):
                yield LedgerRow(r.ts, r.account_id, r.strategy_id, r.symbol, r.side,
                                r.qty, r.price, r.fee, r.realized_pnl, r.fill_id)

    # ----------------------------------------------------------------- audit
    def _audit_row(self, ts: datetime, kind: str, detail: Mapping[str, Any]) -> RiskAudit:
        return RiskAudit(namespace=self.namespace, ts=ts, kind=kind, detail=dict(detail))

    def append_audit(self, kind: str, detail: Mapping[str, Any], ts: datetime | None = None) -> None:
        with get_session() as s:
            s.add(self._audit_row(ts or _utcnow(), kind, detail))

    def append_audit_in(self, session: Any, kind: str, detail: Mapping[str, Any],
                        ts: datetime | None = None) -> None:
        """Add an audit row to the CALLER's open transaction so it commits (or rolls
        back) atomically with the state change it records (2026-10-13, review S-3)."""
        session.add(self._audit_row(ts or _utcnow(), kind, detail))

    def iter_audit(self, kind: str | None = None) -> list[tuple[datetime, str, dict]]:
        with get_session() as s:
            stmt = select(RiskAudit).where(RiskAudit.namespace == self.namespace).order_by(RiskAudit.id)
            if kind is not None:
                stmt = stmt.where(RiskAudit.kind == kind)
            return [(r.ts, r.kind, r.detail) for r in s.scalars(stmt)]


class InMemoryRiskStore:
    """Process-memory RiskStoreLike. No DB. Semantics match RiskStore within a run.

    Intended for backtests and parameter search; a new instance starts empty, so
    ``load(fresh_start=...)`` only initializes (there is no prior process to disarm)."""

    def __init__(self, namespace: str = "backtest") -> None:
        if not namespace:
            raise ValueError("InMemoryRiskStore requires a non-empty namespace")
        self.namespace = namespace
        self._switch: SwitchState | None = None
        self._strategies: dict[str, StrategyState] = {}
        self._ledger: list[LedgerRow] = []
        self._fill_ids: set[str] = set()
        self._audit: list[tuple[datetime, str, dict]] = []

    def load(self, *, fresh_start: bool = True) -> SwitchState:
        now = _utcnow()
        if self._switch is None:
            self._switch = SwitchState(False, "system", None, "initialized")
            self._audit.append((now, "switch_initialized", {"armed": False}))
        elif fresh_start and self._switch.armed:
            self._switch = SwitchState(False, "system", None, "forced disarmed on process start")
            self._audit.append((now, "switch_forced_disarmed_on_restart", {}))
        elif fresh_start:
            self._audit.append((now, "switch_boot_disarmed", {}))
        return self._switch

    def get_switch(self) -> SwitchState:
        return self._switch or SwitchState(False, "", None, "")

    def set_switch(self, *, armed: bool, actor: str, reason: str) -> None:
        now = _utcnow()
        self._switch = SwitchState(armed, actor, now if armed else None, reason)
        self._audit.append((now, "switch_armed" if armed else "switch_disarmed",
                            {"actor": actor, "reason": reason}))

    def get_strategy(self, strategy_id: str) -> StrategyState | None:
        return self._strategies.get(strategy_id)

    def upsert_strategy(self, strategy_id: str, *, enabled: bool | None = None,
                        stage: LadderStage | None = None, size_multiplier: float | None = None,
                        disabled_reason: str | None = None) -> StrategyState:
        cur = self._strategies.get(strategy_id) or StrategyState(
            strategy_id, True, LadderStage.SHADOW, 1.0, "")
        new = StrategyState(
            strategy_id,
            cur.enabled if enabled is None else enabled,
            cur.stage if stage is None else stage,
            cur.size_multiplier if size_multiplier is None else size_multiplier,
            cur.disabled_reason if disabled_reason is None else disabled_reason)
        self._strategies[strategy_id] = new
        return new

    def has_fill(self, fill_id: str) -> bool:
        return fill_id in self._fill_ids

    def append_loss(self, *, ts: datetime, account_id: str, strategy_id: str, symbol: str,
                    side: str, qty: int, price: float, fee: float, realized_pnl: float,
                    fill_id: str) -> bool:
        if fill_id in self._fill_ids:
            return False
        self._fill_ids.add(fill_id)
        self._ledger.append(LedgerRow(ts, account_id, strategy_id, symbol, side, qty, price,
                                      fee, realized_pnl, fill_id))
        return True

    def iter_ledger(self) -> Iterator[LedgerRow]:
        return iter(list(self._ledger))

    def append_audit(self, kind: str, detail: Mapping[str, Any], ts: datetime | None = None) -> None:
        self._audit.append((ts or _utcnow(), kind, dict(detail)))

    def iter_audit(self, kind: str | None = None) -> list[tuple[datetime, str, dict]]:
        return [row for row in self._audit if kind is None or row[1] == kind]
