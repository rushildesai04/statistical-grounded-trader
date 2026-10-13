"""sgt.indicators — streaming + vectorized indicator library.

Public API (see docs/IMPLEMENTATION_PLAN.md §6):
- ``build(spec, ctx)``      -> streaming :class:`~sgt.core.interfaces.Indicator`
- ``batch(spec, frame, ctx)`` -> vectorized :class:`pandas.DataFrame`
- ``validate_spec(spec)``   -> resolved params dict, raises ``ValueError``
- ``KINDS``                 -> kind -> :class:`KindInfo` schema
- ``IndicatorBank``         -> per-symbol bank with ``snapshot()``
- ``IndicatorContext``      -> per-symbol reference facts (free float, ...)
- ``frame_from_bars``       -> bars -> research DataFrame
"""
from __future__ import annotations

from .registry import (
    KINDS,
    IndicatorBank,
    IndicatorContext,
    KindInfo,
    ParamSpec,
    batch,
    build,
    frame_from_bars,
    validate_spec,
)

__all__ = [
    "KINDS",
    "KindInfo",
    "ParamSpec",
    "IndicatorBank",
    "IndicatorContext",
    "build",
    "batch",
    "validate_spec",
    "frame_from_bars",
]
