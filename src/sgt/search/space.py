"""Declarative parameter/combination search space (plan §10).

A :class:`SearchSpace` is loaded from YAML and names a *base* strategy plus the
axes along which to vary it. Axes address parameters by JSON-pointer-like paths into
the raw strategy dict::

    trigger.fast                    -> dict["trigger"]["fast"]
    confirm.signals[0].value        -> positional list index
    exits.any_of[stop_loss_pct].pct -> list element whose "kind" == "stop_loss_pct"

In addition to scalar axes, a space may swap whole components: ``trigger_alternatives``
(a pool of full trigger dicts) and a ``confirm_pool`` (a pool of confirm signals with
``k_of_m`` choices and ``choose`` sizes). The cross product of every axis and component
choice is the raw grid; its size is hard-capped (default 5000, plan §10) so an
unbounded grid fails fast instead of running for hours.

``enumerate_configs`` applies each combination to a fresh copy of the base dict, runs
``validate_strategy`` (which also enforces cross ordering, ``fast < slow``), and yields
only grammar-valid ``(StrategyConfig, params)`` pairs.
Invalid combinations are skipped and counted by reason — enumeration never crashes on a
nonsense cell.
"""
from __future__ import annotations

import copy
import itertools
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from sgt.core.ids import config_hash as _config_hash
from sgt.strategy.schema import StrategyConfig
from sgt.strategy.validate import StrategyValidationError, validate_strategy

_MAX_SPACE_BYTES = 1_000_000
_SEG = re.compile(r"^([^.\[\]]+)(?:\[([^\]]+)\])?$")


class CombinationCapError(ValueError):
    """Raised when the raw grid exceeds ``max_combinations`` (forces bounded grids)."""


class ConfirmPool(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    signals: list[dict] = Field(min_length=1)
    k_of_m: list[int] = Field(min_length=1)
    choose: list[int] | None = None  # pool-subset sizes m; default: full pool size

    def combinations(self) -> list[tuple[int, list[dict]]]:
        """Every (k, chosen-signals) confirm block this pool permits."""
        sizes = self.choose or [len(self.signals)]
        out: list[tuple[int, list[dict]]] = []
        for m in sizes:
            if not 1 <= m <= len(self.signals):
                raise ValueError(f"confirm_pool.choose size {m} out of range 1..{len(self.signals)}")
            for subset in itertools.combinations(range(len(self.signals)), m):
                for k in self.k_of_m:
                    if 1 <= k <= m:
                        out.append((k, [copy.deepcopy(self.signals[i]) for i in subset]))
        return out


class SearchSpace(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base: str
    symbols: list[str] = Field(default_factory=list)
    axes: dict[str, list[Any]] = Field(default_factory=dict)
    trigger_alternatives: list[dict] = Field(default_factory=list)
    confirm_pool: ConfirmPool | None = None
    max_combinations: int = Field(default=5000, gt=0)
    min_trades: int = Field(default=20, ge=1)

    @model_validator(mode="after")
    def _non_empty_axes(self) -> "SearchSpace":
        for path, values in self.axes.items():
            if not values:
                raise ValueError(f"axis {path!r} has no values")
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> "SearchSpace":
        p = Path(path)
        if p.stat().st_size > _MAX_SPACE_BYTES:
            raise ValueError(f"search space file {p} exceeds {_MAX_SPACE_BYTES} bytes")
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"search space at {p} must be a mapping")
        return cls.model_validate(raw)

    def base_dict(self) -> dict:
        return _load_base(self.base)

    def raw_grid_size(self) -> int:
        """Number of cells before validity filtering (what the cap limits)."""
        n = 1
        for values in self.axes.values():
            n *= len(values)
        if self.trigger_alternatives:
            n *= len(self.trigger_alternatives)
        if self.confirm_pool is not None:
            n *= max(1, len(self.confirm_pool.combinations()))
        return n


def _load_base(base: str) -> dict:
    p = Path(base)
    if p.stat().st_size > _MAX_SPACE_BYTES:
        raise ValueError(f"base strategy file {p} exceeds {_MAX_SPACE_BYTES} bytes")
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"base strategy at {p} must be a mapping")
    return raw


@dataclass(slots=True)
class SkipCounts:
    """Tally of combinations skipped during enumeration, keyed by reason bucket."""

    reasons: Counter = field(default_factory=Counter)
    total_yielded: int = 0

    def record(self, reason: str) -> None:
        self.reasons[reason] += 1

    @property
    def total_skipped(self) -> int:
        return int(sum(self.reasons.values()))

    def as_dict(self) -> dict:
        return {"yielded": self.total_yielded, "skipped": self.total_skipped,
                "by_reason": dict(self.reasons)}


def _set_path(root: dict, path: str, value: Any) -> None:
    segs = path.split(".")
    node: Any = root
    for i, seg in enumerate(segs):
        m = _SEG.match(seg)
        if m is None:
            raise ValueError(f"bad path segment {seg!r} in {path!r}")
        name, sel = m.group(1), m.group(2)
        last = i == len(segs) - 1
        container = node[name]
        if sel is None:
            if last:
                node[name] = value
            else:
                node = container
            continue
        # selector into a list: positional index or match by "kind"
        if not isinstance(container, list):
            raise ValueError(f"path {path!r}: {name!r} is not a list")
        idx = _resolve_index(container, sel, path)
        if last:
            raise ValueError(f"path {path!r} must address a scalar field, not a list element")
        node = container[idx]


def _resolve_index(container: list, sel: str, path: str) -> int:
    if sel.lstrip("-").isdigit():
        idx = int(sel)
        if not -len(container) <= idx < len(container):
            raise ValueError(f"path {path!r}: index {idx} out of range")
        return idx
    for i, elem in enumerate(container):
        if isinstance(elem, dict) and elem.get("kind") == sel:
            return i
    raise ValueError(f"path {path!r}: no list element with kind=={sel!r}")


def _bucket(err: StrategyValidationError) -> str:
    blob = " ".join(err.violations).lower()
    if "fast < slow" in blob:
        return "fast_ge_slow"
    if "family" in blob:
        return "same_family_confirm"
    if "correlation" in blob:
        return "correlated_confirm"
    if "k_of_m" in blob:
        return "k_of_m"
    if "trigger" in blob:
        return "trigger_role"
    if "exits must include" in blob:
        return "missing_exit"
    return "schema"


def enumerate_configs(
    space: SearchSpace, *, skips: SkipCounts | None = None
) -> Iterator[tuple[StrategyConfig, dict]]:
    """Yield grammar-valid ``(StrategyConfig, params)`` for every valid grid cell.

    Raises :class:`CombinationCapError` if the raw grid exceeds
    ``space.max_combinations`` (raise the cap explicitly to allow larger grids).
    Invalid cells are skipped and tallied in ``skips`` (reason buckets)."""
    size = space.raw_grid_size()
    if size > space.max_combinations:
        raise CombinationCapError(
            f"raw grid has {size} combinations > cap {space.max_combinations}; "
            "tighten the grid or raise max_combinations explicitly")
    counts = skips if skips is not None else SkipCounts()
    base = space.base_dict()

    axis_paths = sorted(space.axes)
    axis_values = [space.axes[p] for p in axis_paths]
    trigger_choices: list[tuple[int, dict] | None] = (
        [(i, t) for i, t in enumerate(space.trigger_alternatives)]
        if space.trigger_alternatives else [None])
    confirm_choices: list[tuple[int, list[dict]] | None] = (
        space.confirm_pool.combinations() if space.confirm_pool is not None else [None])

    for axis_combo in itertools.product(*axis_values) if axis_values else [()]:
        for trig in trigger_choices:
            for conf in confirm_choices:
                raw = copy.deepcopy(base)
                params: dict[str, Any] = {}
                for path, val in zip(axis_paths, axis_combo):
                    _set_path(raw, path, val)
                    params[path] = val
                if trig is not None:
                    raw["trigger"] = copy.deepcopy(trig[1])
                    params["trigger"] = trig[0]
                if conf is not None:
                    raw["confirm"] = {"k_of_m": conf[0], "signals": conf[1]}
                    params["confirm_k"] = conf[0]
                    params["confirm_m"] = len(conf[1])
                yield from _validate_cell(raw, params, counts)


def _validate_cell(
    raw: dict, params: dict, counts: SkipCounts
) -> Iterator[tuple[StrategyConfig, dict]]:
    try:
        config = validate_strategy(raw)
    except StrategyValidationError as e:
        counts.record(_bucket(e))
        return
    counts.total_yielded += 1
    yield config, params


def params_hash(params: dict) -> str:
    return _config_hash(params)
