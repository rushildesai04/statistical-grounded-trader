"""sgt.learning — lightweight learning, validation statistics, decay monitors.

See docs/IMPLEMENTATION_PLAN.md §9, §10, §11.4.
"""
from sgt.learning.decay import (
    Action,
    CusumMonitor,
    DecaySupervisor,
    DrawdownBudgetMonitor,
    MonitorSignal,
    PSIMonitor,
    RollingExpectancyMonitor,
    SlippageDriftMonitor,
    WinRateMonitor,
)
from sgt.learning.labels import (
    BarrierOutcome,
    InsufficientBarsError,
    label_candidates,
    triple_barrier,
)
from sgt.learning.meta_filter import GBMMetaFilter, LogisticMetaFilter
from sgt.learning.promotion import PromotionDecision, promotion_gate
from sgt.learning.validation import (
    AcceptanceReport,
    bootstrap_ci,
    cpcv_split_count,
    cpcv_splits,
    deflated_sharpe_ratio,
    evaluate_acceptance,
    expectancy,
    expected_max_sharpe,
    max_drawdown,
    probabilistic_sharpe_ratio,
    profit_factor,
    purged_walk_forward_splits,
)

__all__ = [
    "Action", "CusumMonitor", "DecaySupervisor", "DrawdownBudgetMonitor", "MonitorSignal",
    "PSIMonitor", "RollingExpectancyMonitor", "SlippageDriftMonitor", "WinRateMonitor",
    "BarrierOutcome", "InsufficientBarsError", "label_candidates", "triple_barrier",
    "GBMMetaFilter", "LogisticMetaFilter",
    "PromotionDecision", "promotion_gate",
    "AcceptanceReport", "bootstrap_ci", "cpcv_split_count", "cpcv_splits",
    "deflated_sharpe_ratio", "evaluate_acceptance", "expectancy", "expected_max_sharpe",
    "max_drawdown", "probabilistic_sharpe_ratio", "profit_factor", "purged_walk_forward_splits",
]
