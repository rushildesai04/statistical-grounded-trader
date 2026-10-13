"""Cross-package interfaces for the intraday engine.

Implementations live in their owning packages:
- ``Indicator``          -> sgt.indicators
- ``HistoricalBarSource``, ``LiveBarSource`` -> sgt.intraday.sources
- ``BrokerAdapter``      -> sgt.brokers (SimBroker, Alpaca, AlertOnly, ...)
- ``RiskGate``           -> sgt.risk
- ``MetaFilter``         -> sgt.learning
- ``EventSink``          -> sgt.engine.eventlog
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, AsyncIterator, Iterable, Mapping, Protocol, Sequence, runtime_checkable

from sgt.core.types import (
    AccountState,
    Bar,
    Fill,
    HaltEvent,
    OrderAck,
    OrderIntent,
    RiskDecision,
)

# --------------------------------------------------------------------------- indicators


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    """Identifies one parameterized indicator instance, e.g. EMA(9).

    ``params`` is a sorted tuple of (name, value) so specs are hashable and their
    ``key`` is canonical: ``"ema(n=9)"``, ``"macd(fast=12,signal=9,slow=26)"``.
    """

    kind: str
    params: tuple[tuple[str, float | int | str], ...] = ()

    @classmethod
    def of(cls, kind: str, **params: float | int | str) -> "IndicatorSpec":
        return cls(kind=kind, params=tuple(sorted(params.items())))

    @property
    def key(self) -> str:
        inner = ",".join(f"{k}={v}" for k, v in self.params)
        return f"{self.kind}({inner})"

    def param(self, name: str) -> Any:
        for k, v in self.params:
            if k == name:
                return v
        raise KeyError(f"{self.key} has no param {name!r}")


class Indicator(ABC):
    """Streaming indicator: O(1) work per bar, deterministic, no look-ahead.

    ``update`` consumes bars strictly in time order for ONE symbol. Outputs are
    ``None`` until ``ready``. Multi-output indicators (MACD, PVT, PSAR, ...) list
    their output names in ``outputs``; ``outputs[0]`` is the primary value.
    """

    spec: IndicatorSpec
    outputs: tuple[str, ...]
    warmup: int  # bars required before outputs are valid

    @abstractmethod
    def update(self, bar: Bar) -> None: ...

    @property
    @abstractmethod
    def ready(self) -> bool: ...

    @abstractmethod
    def values(self) -> Mapping[str, float | None]:
        """Current value for every name in ``outputs`` (None while not ready)."""

    def value(self, output: str | None = None) -> float | None:
        return self.values()[output or self.outputs[0]]

    @abstractmethod
    def reset(self) -> None: ...


# --------------------------------------------------------------------------- market data


class HistoricalBarSource(ABC):
    """Vendor-agnostic 1-minute bar history (inclusive start, exclusive end, UTC)."""

    name: str

    @abstractmethod
    def fetch(self, symbol: str, start: datetime, end: datetime) -> list[Bar]: ...


class LiveBarSource(ABC):
    """Yields completed 1-minute bars for a watchlist as they become available.

    ``delay_minutes`` is the feed's inherent delay (15 for free delayed SIP, 0 for
    paid real-time). Live trading must refuse a source with delay > 0 or an
    IEX-only feed; see ``is_live_tradeable``.
    """

    name: str
    delay_minutes: int
    consolidated: bool  # True for SIP (all exchanges), False for single-venue (IEX)

    @property
    def is_live_tradeable(self) -> bool:
        return self.delay_minutes == 0 and self.consolidated

    @abstractmethod
    def stream(self, symbols: Sequence[str]) -> AsyncIterator[Bar]: ...

    @abstractmethod
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ReferenceData:
    """Point-in-time reference facts for one symbol on one session date."""

    symbol: str
    as_of: date
    shares_outstanding: float | None = None
    free_float: float | None = None
    float_source: str = ""        # "edgar_dei", "vendor:<name>", "manual"
    dilution_flag: bool = False   # recent S-1/S-3/424B*/F-1/F-3 filing within lookback
    short_interest_pct_float: float | None = None


# --------------------------------------------------------------------------- execution


class BrokerAdapter(ABC):
    """One brokerage account connection. All methods normalize to core types.

    Contract (enforced by tests/brokers/test_contract.py for every adapter):
    - ``submit`` is idempotent on ``intent.client_order_id``: a second submit with
      the same id returns the original ack and never creates a second order.
    - ``fills()`` yields each execution exactly once (dedupe on ``Fill.fill_id``).
    - Network/auth failures raise ``BrokerError`` subclasses; adapters never swallow them.
    """

    name: str
    account_id: str
    supports_extended_hours: bool
    supports_broker_side_stops: bool
    is_paper: bool

    @abstractmethod
    async def connect(self) -> None: ...

    @abstractmethod
    async def account_state(self) -> AccountState: ...

    @abstractmethod
    async def submit(self, intent: OrderIntent) -> OrderAck: ...

    @abstractmethod
    async def cancel(self, client_order_id: str) -> OrderAck: ...

    @abstractmethod
    async def open_orders(self) -> list[OrderAck]: ...

    @abstractmethod
    def fills(self) -> AsyncIterator[Fill]: ...

    @abstractmethod
    async def health(self) -> "AdapterHealth": ...

    @abstractmethod
    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AdapterHealth:
    ok: bool
    detail: str = ""
    auth_expires_at: datetime | None = None


class BrokerError(Exception):
    """Base for adapter failures. ``retryable`` tells callers whether a retry is safe."""

    retryable: bool = False


class BrokerAuthError(BrokerError):
    retryable = False


class BrokerRejectedError(BrokerError):
    retryable = False


class BrokerUnavailableError(BrokerError):
    retryable = True


# --------------------------------------------------------------------------- risk


@dataclass(frozen=True, slots=True)
class TradeContext:
    """Market facts the risk gate needs for one pre-trade check (all point-in-time)."""

    now: datetime
    mark_price: float
    session_window: str                    # e.g. "power_hour", "post", "midday"
    halted: bool = False
    spread_pct: float | None = None        # (ask-bid)/mid*100 when quotes available
    luld_distance_pct: float | None = None # distance to nearest LULD band, % of price
    stop_price: float | None = None        # strategy's protective stop for entries
    bar_volume: float | None = None        # last completed bar volume (participation cap)


@runtime_checkable
class RiskGate(Protocol):
    """Pre-trade check. Must fail closed: any internal error => REJECT.

    Exits (SELL that reduces an existing long) are never blocked by budget/limit
    rules — only by hard impossibilities (halted, no position). Blocking an exit
    would turn a guardrail into a loss amplifier.
    """

    def check(self, intent: OrderIntent, ctx: TradeContext) -> RiskDecision: ...

    def on_fill(self, fill: Fill) -> None: ...


# --------------------------------------------------------------------------- learning


@runtime_checkable
class MetaFilter(Protocol):
    """Lightweight take/skip model over a candidate's point-in-time features."""

    model_id: str
    feature_names: tuple[str, ...]

    def score(self, features: Mapping[str, float]) -> float:
        """Calibrated probability the candidate hits its profit barrier first."""
        ...

    def explain(self, features: Mapping[str, float], top_k: int = 5) -> list[tuple[str, float]]:
        """Top feature contributions (name, signed contribution)."""
        ...


# --------------------------------------------------------------------------- audit


class EventSink(ABC):
    """Append-only audit/event log. ``emit`` must never silently drop an event."""

    @abstractmethod
    def emit(self, kind: str, ts: datetime, payload: Mapping[str, Any]) -> None: ...

    @abstractmethod
    def flush(self) -> None: ...


def iter_halts(events: Iterable[HaltEvent], symbol: str) -> list[HaltEvent]:
    """Halt events for one symbol, time-ordered."""
    return sorted((e for e in events if e.symbol == symbol), key=lambda e: e.ts)
