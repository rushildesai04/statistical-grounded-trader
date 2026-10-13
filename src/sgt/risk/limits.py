"""Validated risk configuration (capital-protection doctrine, IMPLEMENTATION_PLAN §1, §11.4-11.6).

These are the hard limits the risk layer enforces BEFORE any order reaches a broker
adapter. Everything here is a pydantic v2 model validated at load time so a malformed
YAML/dict fails closed at startup rather than silently disabling a guardrail.

All dollar amounts are USD; percentages are whole-number percent (1.5 == 1.5%);
``max_participation`` is a fraction in (0, 1].
"""
from __future__ import annotations

import enum
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator


class LadderStage(str, enum.Enum):
    """Capital ladder stage (IMPLEMENTATION_PLAN §11.5).

    SHADOW places NO live orders at all. MICRO/SMALL/STANDARD carry increasing
    per-strategy capital-at-risk caps. Promotion is human-only; demotion automatic.
    """

    SHADOW = "shadow"
    MICRO = "micro"
    SMALL = "small"
    STANDARD = "standard"

    @property
    def rank(self) -> int:
        return _STAGE_ORDER.index(self)

    @property
    def is_live(self) -> bool:
        return self is not LadderStage.SHADOW

    def demoted(self) -> "LadderStage":
        """The next lower rung (SHADOW is the floor)."""
        return _STAGE_ORDER[max(0, self.rank - 1)]


_STAGE_ORDER: tuple[LadderStage, ...] = (
    LadderStage.SHADOW,
    LadderStage.MICRO,
    LadderStage.SMALL,
    LadderStage.STANDARD,
)


class StageAllocations(BaseModel):
    """Per-stage maximum capital-at-risk (dollar notional) per strategy.

    Defaults follow §11.5: shadow $0, micro $500, small $2,000, standard = owner cap.
    The owner cap defaults to a conservative $10,000 and MUST be set deliberately.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    shadow: float = Field(default=0.0, ge=0.0)
    micro: float = Field(default=500.0, ge=0.0)
    small: float = Field(default=2_000.0, ge=0.0)
    standard: float = Field(default=10_000.0, ge=0.0)

    @model_validator(mode="after")
    def _check_monotone(self) -> "StageAllocations":
        if self.shadow != 0.0:
            raise ValueError("StageAllocations.shadow must be 0 (shadow places no live orders)")
        if not (self.micro <= self.small <= self.standard):
            raise ValueError("stage allocations must be non-decreasing: micro <= small <= standard")
        return self

    def allocation_for(self, stage: LadderStage) -> float:
        return {
            LadderStage.SHADOW: self.shadow,
            LadderStage.MICRO: self.micro,
            LadderStage.SMALL: self.small,
            LadderStage.STANDARD: self.standard,
        }[stage]


class RiskLimits(BaseModel):
    """Hard pre-trade limits. Any value here is a guardrail; relax deliberately."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_notional_per_trade: float = Field(gt=0.0)
    max_loss_per_trade: float = Field(gt=0.0)
    daily_loss_limit_per_account: float = Field(gt=0.0)
    daily_loss_limit_global: float = Field(gt=0.0)
    daily_profit_lock_pct: float = Field(ge=0.0)  # 0 disables profit lock
    max_trades_per_day: int = Field(gt=0)
    max_open_positions: int = Field(gt=0)
    max_symbol_notional_total: float = Field(gt=0.0)
    min_price: float = Field(gt=0.0)
    max_spread_pct: float = Field(gt=0.0)
    max_participation: float = Field(gt=0.0, le=1.0)
    luld_buffer_pct: float = Field(ge=0.0)
    allowed_windows: frozenset[str] = Field(default=frozenset({"rth", "power_hour", "after_hours"}))
    cash_account: bool = False

    @field_serializer("allowed_windows")
    def _serialize_windows(self, windows: frozenset[str]) -> list[str]:
        # Deterministic serialization regardless of hash seed (2026-10-13, F-11).
        return sorted(windows)

    @field_validator("allowed_windows", mode="before")
    @classmethod
    def _coerce_windows(cls, v: Any) -> Any:
        if v is None:
            return frozenset()
        if isinstance(v, (list, tuple, set)):
            return frozenset(str(x) for x in v)
        return v


class LossBudgets(BaseModel):
    """Fail-closed loss budgets (§11.6). Enforced on realized LIVE PnL.

    ``learning_budget_total`` caps cumulative net realized loss for the whole program
    before it auto-disarms. ``per_strategy_budget`` is the default per-strategy cap;
    ``overrides`` sets exceptions per strategy id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    learning_budget_total: float = Field(gt=0.0)
    per_strategy_budget: float = Field(gt=0.0)
    overrides: dict[str, float] = Field(default_factory=dict)

    @field_validator("overrides")
    @classmethod
    def _positive_overrides(cls, v: dict[str, float]) -> dict[str, float]:
        for sid, amt in v.items():
            if amt <= 0:
                raise ValueError(f"per-strategy budget override for {sid!r} must be > 0")
        return v

    def budget_for(self, strategy_id: str) -> float:
        return self.overrides.get(strategy_id, self.per_strategy_budget)


class RiskConfig(BaseModel):
    """Top-level risk configuration, loadable from a dict or YAML file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    limits: RiskLimits
    budgets: LossBudgets
    stages: StageAllocations = Field(default_factory=StageAllocations)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RiskConfig":
        return cls.model_validate(dict(data))

    @classmethod
    def from_yaml(cls, path: str) -> "RiskConfig":
        import yaml  # local import: yaml is a base dependency but keep import cost off hot paths

        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        if not isinstance(raw, Mapping):
            raise ValueError(f"risk config at {path} must be a mapping, got {type(raw).__name__}")
        return cls.from_dict(raw)
