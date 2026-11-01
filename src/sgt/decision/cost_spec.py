"""Serializable cost-source spec: train/serve cost parity (F-2, 2026-10-18).

The cost a model was trained against enters inference twice: as the ``f_cost_rt_pct``
feature and as the policy hurdle. A runtime that prices costs with a different model than
the dataset did shifts both silently. The dataset records a spec of its cost source in
``meta.json``; the trainer copies it into the artifact metadata; every runtime (live,
replay, backtest) rebuilds the SAME source from the artifact. A missing spec fails closed.
"""
from __future__ import annotations

from dataclasses import asdict, fields
from typing import Any, Callable, Mapping

import numpy as np
import pandas as pd

from sgt.brokers.costs import CostModel
from sgt.decision.features import CostModelAdapter, CostSource

_BUCKET_FIELDS = {f.name for f in fields(CostModel)}


def spec_of(source: CostSource) -> dict[str, Any]:
    """JSON-safe description of a cost source."""
    from sgt.brokers.measured_costs import MeasuredCostModel

    if isinstance(source, CostModelAdapter):
        return {"kind": "bucket", "tag": source.tag, "model": asdict(source.model),
                "open_hour_mult": source.open_hour_mult, "rest_mult": source.rest_mult}
    if isinstance(source, MeasuredCostModel):
        return {"kind": "measured", "base": asdict(source._base),
                "min_samples": source._min_samples}
    raise TypeError(f"cannot serialize cost source {type(source).__name__}")


def source_from_spec(spec: Mapping[str, Any] | None, *,
                     session_factory: Callable[[], object] | None = None) -> CostSource:
    """Rebuild the cost source a model was trained with. Unknown/missing spec -> ValueError."""
    if not spec:
        raise ValueError("model artifact carries no cost_spec; refusing to guess its costs (F-2)")
    kind = spec.get("kind")
    if kind == "bucket":
        model = _bucket(spec.get("model") or {})
        return CostModelAdapter(model, tag=str(spec.get("tag", "model")),
                                open_hour_mult=float(spec.get("open_hour_mult", 1.0)),
                                rest_mult=float(spec.get("rest_mult", 1.0)))
    if kind == "measured":
        from sgt.brokers.measured_costs import MeasuredCostModel
        from sgt.db.session import get_session

        return MeasuredCostModel.from_db(session_factory or get_session,
                                         base=_bucket(spec.get("base") or {}),
                                         min_samples=int(spec.get("min_samples", 3)))
    raise ValueError(f"unknown cost_spec kind {kind!r}")


def _bucket(d: Mapping[str, Any]) -> CostModel:
    unknown = set(d) - _BUCKET_FIELDS
    if unknown:
        raise ValueError(f"unknown CostModel fields in cost_spec: {sorted(unknown)}")
    return CostModel(**{k: float(v) for k, v in d.items()})


def check_bucket_spec_matches(df: pd.DataFrame, spec: Mapping[str, Any], *,
                              sample: int = 20_000, tol: float = 1e-6) -> float:
    """Verify a bucket spec reproduces the dataset's ``f_cost_rt_pct`` feature.

    The feature is ``100 * round_trip(close)`` at the signal bar and ``f_log_price`` is
    ``ln(close)``, so a correct spec matches row-for-row. Returns the fraction of sampled
    rows that match; used to attach a spec to datasets built before specs were recorded.
    """
    if spec.get("kind") != "bucket":
        raise ValueError("only bucket specs can be verified from features")
    src = source_from_spec(spec)
    d = df[["f_log_price", "f_cost_rt_pct"]].dropna()
    if d.empty:
        return float("nan")
    d = d.sample(min(sample, len(d)), random_state=0)
    prices = np.exp(d["f_log_price"].to_numpy())
    want = d["f_cost_rt_pct"].to_numpy() / 100.0
    got = np.array([src.round_trip("X", None, float(p))[0] for p in prices])  # type: ignore[arg-type]
    # A price sitting exactly on a bucket edge can round across it via exp(log(p)).
    return float(np.mean(np.abs(got - want) <= tol + 1e-9 * np.abs(want)))
