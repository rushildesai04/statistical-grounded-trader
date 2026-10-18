"""Foundation types for the declarative strategy grammar (plan §7).

Everything a condition needs to make a point-in-time decision is carried on an
:class:`EvalView`: the current indicator snapshot (flat ``"<spec.key>.<output>"``
dict), the previous bar's snapshot, the bar itself, position state, the named
session windows, point-in-time reference data, and bounded per-symbol history ring
buffers. Conditions are pure: ``evaluate(view) -> bool`` and MUST return ``False``
(never raise) when any input they need is missing or ``None`` (warm-up), so a
cold indicator simply makes a condition false rather than crashing the engine.

The condition classes themselves live in :mod:`sgt.strategy.conditions` and
:mod:`sgt.strategy.exits`; this module holds only the shared contracts so those
two modules (and the schema) can import without cycles.
"""
from __future__ import annotations

import enum
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from sgt.core.interfaces import IndicatorSpec, ReferenceData
from sgt.core.types import Bar


class Family(str, enum.Enum):
    """Signal family (plan §7.1). The validator uses this to forbid nonsense
    (a confirmation from the trigger's own family, a context signal as trigger)."""

    TREND = "trend"
    MOMENTUM = "momentum"
    FLOW = "flow"
    VOLATILITY = "volatility"
    STRUCTURE = "structure"
    CONTEXT = "context"


class Role(str, enum.Enum):
    """Where a condition may be used in a strategy."""

    TRIGGER = "trigger"
    SETUP = "setup"
    CONFIRM = "confirm"
    FILTER = "filter"
    REGIME = "regime"
    EXIT = "exit"


# --------------------------------------------------------------------------- views


@dataclass(frozen=True, slots=True)
class PositionView:
    """Open-position facts exposed to exit conditions.

    ``entry_atr`` (additive, 2026-10-17, DECISION_MODEL.md §5 F1) is the ATR frozen at
    the signal bar used to size the position. The model-strategy ATR brackets
    (:class:`~sgt.strategy.exits.AtrTakeProfit` / :class:`~sgt.strategy.exits.AtrStop`)
    read it so the runtime barriers equal the FIXED signal-bar barriers the training
    labels used, instead of drifting with the live ATR. ``None`` (the default for rule
    strategies, which never set it) keeps the previous live-ATR behavior.
    """

    qty: int
    entry_price: float
    entry_ts: datetime
    high_since_entry: float
    bars_held: int
    entry_atr: float | None = None

    def minutes_held(self, now: datetime) -> float:
        return (now - self.entry_ts).total_seconds() / 60.0


@dataclass(frozen=True, slots=True)
class HistoryView:
    """Bounded ring-buffer view of the bars *strictly before* the current one,
    oldest first. Lengths are equal; ``snaps[i]`` is the indicator snapshot taken
    on the bar whose OHLCV is at index ``i``."""

    closes: Sequence[float]
    highs: Sequence[float]
    lows: Sequence[float]
    volumes: Sequence[float]
    snaps: Sequence[Mapping[str, float | None]]

    def __len__(self) -> int:
        return len(self.closes)


@dataclass(frozen=True, slots=True)
class EvalView:
    """Everything a condition may read for one symbol on one bar close."""

    bar: Bar
    snap_now: Mapping[str, float | None]
    snap_prev: Mapping[str, float | None] | None
    windows: frozenset[str]
    history: HistoryView
    position: PositionView | None = None
    reference: ReferenceData | None = None
    session_high_prior: float | None = None   # max high of PRE+RTH bars before now
    premarket_high: float | None = None        # max high of PRE bars this session

    def now(self, key: str) -> float | None:
        return self.snap_now.get(key)

    def prev(self, key: str) -> float | None:
        return self.snap_prev.get(key) if self.snap_prev is not None else None


# --------------------------------------------------------------------------- decisions / candidates


@dataclass(frozen=True, slots=True)
class Decision:
    """A strategy intent emitted on a bar close. The executor turns an ``entry``
    into a risk-checked order (filled no earlier than the next bar), an ``exit``
    into a protective-stop cancel + sell, and a ``cancel`` into an order cancel."""

    kind: str                       # "entry" | "exit" | "cancel"
    symbol: str
    reason: str
    signal_ts: datetime
    limit_price: float | None = None
    stop_price: float | None = None
    qty_hint: int | None = None
    features: Mapping[str, float] = ()  # point-in-time feature snapshot (entries)


@dataclass(slots=True)
class Candidate:
    """A trigger firing, taken or skipped, with its full point-in-time features.

    Mutable so the executor can finalize ``taken``/``skip_reason`` after the risk
    gate runs (a strategy-approved candidate the risk layer rejects becomes
    ``taken=False, skip_reason='risk'``)."""

    ts: datetime
    symbol: str
    taken: bool
    skip_reason: str | None
    features: dict[str, float]


# --------------------------------------------------------------------------- condition base

_DECISION_KINDS = ("entry", "exit", "cancel")


class Condition(BaseModel):
    """Base for every grammar condition. Subclasses set the three class vars and
    implement :meth:`required_specs` and :meth:`evaluate`. ``kind`` is the pydantic
    discriminator (a ``Literal`` on each subclass)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    FAMILY: ClassVar[Family]
    ROLES: ClassVar[frozenset[Role]]
    CORRELATION_GROUP: ClassVar[str | None] = None

    def required_specs(self) -> list[IndicatorSpec]:
        """Indicator specs this condition reads from the snapshot."""
        return []

    def history_window(self) -> int:
        """How many prior bars this condition needs in the ring buffer (0 = none)."""
        return 0

    def evaluate(self, view: EvalView) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    # -- helpers for subclasses ------------------------------------------------
    @staticmethod
    def _out(spec: IndicatorSpec, output: str) -> str:
        return f"{spec.key}.{output}"
