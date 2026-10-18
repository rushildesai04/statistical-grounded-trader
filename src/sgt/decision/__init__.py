"""Decision model: point-in-time features, candidate triggers, ATR-bracket labels,
the streaming dataset builder, and the model-driven strategy runtime.

See docs/DECISION_MODEL.md. The trainer/policy (``models``, ``policy``, ``train``)
live alongside these modules and consume the dataset built here; this package owns the
feature/trigger/label contract and the :class:`~sgt.decision.runtime.DecisionRuntime`.
"""
from __future__ import annotations

from sgt.decision.features import (
    FEATURE_NAMES,
    FLOAT_DEPENDENT_FEATURES,
    CostModelAdapter,
    CostSource,
    FeatureContext,
    compute_features,
    feature_indicator_specs,
)
from sgt.decision.labels import (
    BracketOutcome,
    LabelConfig,
    atr_bracket_label,
    default_grid,
    uniqueness_weights,
)
from sgt.decision.triggers import TRIGGER_NAMES, TRIGGERS, fired_trigger

__all__ = [
    "FEATURE_NAMES", "FLOAT_DEPENDENT_FEATURES", "CostModelAdapter", "CostSource",
    "FeatureContext", "compute_features", "feature_indicator_specs",
    "BracketOutcome", "LabelConfig", "atr_bracket_label", "default_grid",
    "uniqueness_weights", "TRIGGER_NAMES", "TRIGGERS", "fired_trigger",
]
