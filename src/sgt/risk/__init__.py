"""sgt.risk — capital-protection / risk layer (docs/IMPLEMENTATION_PLAN.md §1, §11.4-11.6).

Public API:
- RiskConfig / RiskLimits / LossBudgets / StageAllocations / LadderStage  (sgt.risk.limits)
- RiskManager (the RiskGate implementation)                               (sgt.risk.manager)
- RiskStore + state dataclasses                                           (sgt.risk.state)
- ExposureView protocol + DictExposureView                               (sgt.risk.exposure)
- SettlementTracker                                                      (sgt.risk.settlement)
"""
from __future__ import annotations

from sgt.risk.exposure import DictExposureView, ExposureView
from sgt.risk.limits import (
    LadderStage,
    LossBudgets,
    RiskConfig,
    RiskLimits,
    StageAllocations,
)
from sgt.risk.manager import RiskManager
from sgt.risk.settlement import SettlementTracker
from sgt.risk.state import InMemoryRiskStore, RiskStore, RiskStoreLike, StrategyState, SwitchState

__all__ = [
    "DictExposureView",
    "ExposureView",
    "LadderStage",
    "LossBudgets",
    "RiskConfig",
    "RiskLimits",
    "StageAllocations",
    "RiskManager",
    "SettlementTracker",
    "RiskStore",
    "InMemoryRiskStore",
    "RiskStoreLike",
    "StrategyState",
    "SwitchState",
]
