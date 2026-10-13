"""Validation of the pydantic risk-config models (fail at load, not at trade time)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from sgt.risk import LadderStage, LossBudgets, RiskConfig, StageAllocations

from .conftest import make_config


def test_valid_config_loads():
    cfg = make_config()
    assert cfg.limits.max_trades_per_day == 3
    assert cfg.stages.allocation_for(LadderStage.MICRO) == 500.0
    assert "power_hour" in cfg.limits.allowed_windows


def test_allowed_windows_coerced_from_list():
    cfg = make_config(allowed_windows=["rth", "post"])
    assert cfg.limits.allowed_windows == frozenset({"rth", "post"})


def test_shadow_allocation_must_be_zero():
    with pytest.raises(ValidationError):
        StageAllocations(shadow=10.0)


def test_stage_allocations_must_be_non_decreasing():
    with pytest.raises(ValidationError):
        StageAllocations(micro=2000.0, small=500.0)


def test_missing_required_limit_rejected():
    with pytest.raises(ValidationError):
        RiskConfig.from_dict({"limits": {"max_notional_per_trade": 1000.0},
                              "budgets": {"learning_budget_total": 500.0, "per_strategy_budget": 200.0}})


def test_negative_budget_override_rejected():
    with pytest.raises(ValidationError):
        LossBudgets(learning_budget_total=500.0, per_strategy_budget=200.0, overrides={"s1": -1.0})


def test_budget_for_uses_override():
    b = LossBudgets(learning_budget_total=500.0, per_strategy_budget=200.0, overrides={"s1": 50.0})
    assert b.budget_for("s1") == 50.0
    assert b.budget_for("other") == 200.0


def test_unknown_field_forbidden():
    with pytest.raises(ValidationError):
        StageAllocations(bogus=1.0)
