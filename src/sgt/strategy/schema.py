"""Declarative strategy configuration models (plan §7.2).

These pydantic models define the *shape* of a strategy; the *grammar* rules
(exactly one trigger, confirm family/correlation constraints, mandatory exits,
valid indicator params) are enforced separately in :mod:`sgt.strategy.validate`
so a config can be loaded and then reported against with a full violation list.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from sgt.strategy.conditions import AnyCondition, RelVolGt

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
