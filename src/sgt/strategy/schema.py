"""Declarative strategy configuration models (plan §7.2).

These pydantic models define the *shape* of a strategy; the *grammar* rules
(exactly one trigger, confirm family/correlation constraints, mandatory exits,
valid indicator params) are enforced separately in :mod:`sgt.strategy.validate`
so a config can be loaded and then reported against with a full violation list.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from sgt.strategy.base import Condition
from sgt.strategy.conditions import AnyCondition, RelVolGt
from sgt.strategy.exits import AtrStop, AtrTakeProfit, SessionEnd, TimeStopMinutes

# Lookback (sessions) for the universe's relative-volume floor.
UNIVERSE_RELVOL_LOOKBACK = 20


class Universe(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    float_max: float = Field(gt=0.0)
    price_min: float = Field(gt=0.0)
    price_max: float = Field(default=1.0e9, gt=0.0)
    rel_vol_min: float = Field(default=0.0, ge=0.0)
    spread_max_pct: float = Field(default=100.0, gt=0.0)


class SetupBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    any_of: list[AnyCondition] = Field(min_length=1)


class ConfirmBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    k_of_m: int = Field(ge=1)
    signals: list[AnyCondition] = Field(default_factory=list)


class ExitsBlock(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    any_of: list[AnyCondition] = Field(min_length=1)


class Sizing(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    method: str = "fixed_risk"
    risk_per_trade_pct_of_allocation: float = Field(gt=0.0)
    allocation_usd: float = Field(gt=0.0)
    max_notional_usd: float = Field(gt=0.0)
    entry_offset_bps: float = Field(default=0.0, ge=0.0)
    exit_offset_bps: float = Field(default=0.0, ge=0.0)


class MetaFilterConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    model_path: str
    threshold: float = Field(ge=0.0, le=1.0)
    size_by_score: bool = False


class StrategyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    version: int = 1
    universe: Universe
    regime: list[AnyCondition] = Field(default_factory=list)
    setup: SetupBlock | None = None
    trigger: AnyCondition
    confirm: ConfirmBlock = Field(default_factory=lambda: ConfirmBlock(k_of_m=1, signals=[]))
    filters: list[AnyCondition] = Field(default_factory=list)
    exits: ExitsBlock
    sizing: Sizing
    meta_filter: MetaFilterConfig | None = None
    extended_hours: bool = False

    def universe_conditions(self) -> list[AnyCondition]:
        """Indicator-backed universe gates (2026-10-13, F-8). Price and float are
        checked directly by the runtime; rel-vol needs an indicator, so it is
        expressed as a condition and its spec flows through ``all_conditions``."""
        if self.universe.rel_vol_min > 0:
            return [RelVolGt(lookback=UNIVERSE_RELVOL_LOOKBACK, value=self.universe.rel_vol_min,
                             output="cum")]
        return []

    def all_conditions(self) -> list[AnyCondition]:
        out: list[AnyCondition] = list(self.universe_conditions())
        out.extend(self.regime)
        if self.setup is not None:
            out.extend(self.setup.any_of)
        out.append(self.trigger)
        out.extend(self.confirm.signals)
        out.extend(self.filters)
        out.extend(self.exits.any_of)
        return out


# --------------------------------------------------------------------------- model strategy

class ExitBracket(BaseModel):
    """ATR-scaled exit bracket for a model strategy (docs/DECISION_MODEL.md §5, §9)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    atr_n: int = Field(default=14, ge=1, le=500)
    k_tp: float = Field(gt=0.0)
    k_sl: float = Field(gt=0.0)
    time_stop_minutes: int = Field(gt=0)
    session_end_minutes_before_close: int = Field(default=5, ge=0)


class ModelBlock(BaseModel):
    """Model artifact location + policy hyper-parameters (docs/DECISION_MODEL.md §7)."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    artifact_path: str
    z: float = Field(default=1.0, ge=0.0)
    h: float = Field(default=0.0)
    p_min: float = Field(default=0.5, ge=0.0, le=1.0)
    max_size_fraction: float = Field(default=0.02, gt=0.0, le=1.0)


@dataclass(frozen=True, slots=True)
class _ModelExits:
    """Exit container exposing ``any_of`` so the broker-agnostic order lifecycle and
    the :class:`~sgt.decision.runtime.DecisionRuntime` read model exits exactly as they
    read rule-strategy exits."""

    any_of: tuple[Condition, ...]


class ModelStrategyConfig(BaseModel):
    """A ``kind: model`` strategy: candidate triggers + universe/regime vetoes + an
    ATR exit bracket + the model artifact and policy params. Downstream (engine,
    executor, order lifecycle, risk, brokers) consumes it through the same surface as
    :class:`StrategyConfig` (``id``, ``universe``, ``sizing``, ``exits``,
    ``extended_hours``); the :class:`~sgt.engine.engine.StrategyEngine` builds a
    :class:`~sgt.decision.runtime.DecisionRuntime` instead of a ``StrategyRuntime``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    id: str
    version: int = 1
    kind: Literal["model"] = "model"
    universe: Universe
    regime: list[AnyCondition] = Field(default_factory=list)
    triggers: list[str] = Field(min_length=1)
    bracket: ExitBracket
    sizing: Sizing
    model: ModelBlock
    extended_hours: bool = False

    @field_validator("triggers")
    @classmethod
    def _valid_triggers(cls, v: list[str]) -> list[str]:
        from sgt.decision.triggers import TRIGGER_NAMES

        unknown = [t for t in v if t not in TRIGGER_NAMES]
        if unknown:
            raise ValueError(f"unknown trigger(s) {unknown}; known: {list(TRIGGER_NAMES)}")
        if len(set(v)) != len(v):
            raise ValueError(f"duplicate triggers in {v}")
        return v

    @property
    def triggers_enabled(self) -> frozenset[str]:
        return frozenset(self.triggers)

    @property
    def exits(self) -> _ModelExits:
        """The exit conditions the runtime evaluates each held bar (ATR take-profit,
        ATR stop, time stop, forced session-end flatten)."""
        window = "post" if self.extended_hours else "rth"
        return _ModelExits((
            AtrTakeProfit(k=self.bracket.k_tp, atr_n=self.bracket.atr_n),
            AtrStop(k=self.bracket.k_sl, atr_n=self.bracket.atr_n),
            TimeStopMinutes(n=self.bracket.time_stop_minutes),
            SessionEnd(minutes_before_close=self.bracket.session_end_minutes_before_close,
                       window=window),
        ))

    def all_conditions(self) -> list[AnyCondition]:
        """Grammar conditions whose indicator specs the engine must build for the veto
        checks (regime + universe rel-vol). Triggers/features/exits contribute their
        specs via :func:`sgt.decision.runtime.model_indicator_specs`."""
        out: list[AnyCondition] = list(self.universe_conditions())
        out.extend(self.regime)
        return out

    def universe_conditions(self) -> list[AnyCondition]:
        if self.universe.rel_vol_min > 0:
            return [RelVolGt(lookback=UNIVERSE_RELVOL_LOOKBACK, value=self.universe.rel_vol_min,
                             output="cum")]
        return []
