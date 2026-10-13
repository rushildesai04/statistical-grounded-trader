"""Shared domain types for the intraday engine.

These are the contracts every intraday package (data, indicators, strategy,
engine, brokers, risk, learning, live) builds against. Changing a field here is
a cross-package change: update all producers/consumers and their tests together.

Conventions
- All timestamps are timezone-aware UTC. ``Bar.ts`` is the bar's OPEN time
  (a 1-minute bar stamped 14:31:00Z covers [14:31:00, 14:32:00)).
- Prices are floats in USD; quantities are whole shares (int, > 0).
- Objects are immutable (frozen dataclasses) so they can be logged and replayed
  without defensive copies.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import date, datetime, timezone


def _require_utc(name: str, ts: datetime) -> None:
    if ts.tzinfo is None or ts.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{name} must be tz-aware UTC, got {ts!r}")


class Segment(str, enum.Enum):
    """Trading-day segment a bar belongs to (US equities, ET)."""

    PRE = "pre"    # 04:00-09:30 ET
    RTH = "rth"    # 09:30-16:00 ET (or early close)
    POST = "post"  # 16:00-20:00 ET


@dataclass(frozen=True, slots=True)
class Bar:
    """One 1-minute OHLCV bar, annotated with its session position.

    ``session_date`` is the exchange session (ET calendar date) the bar belongs to;
    ``minute_of_day`` is minutes since ET midnight of the bar open (e.g. 09:30 -> 570).
    Both are produced by ``sgt.intraday.calendar`` so every consumer agrees on
    session boundaries.
    """

    symbol: str
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    session_date: date
    minute_of_day: int
    segment: Segment
    vwap: float | None = None          # vendor-provided bar VWAP if available
    trade_count: int | None = None

    def __post_init__(self) -> None:
        _require_utc("Bar.ts", self.ts)
        if not self.symbol or self.symbol != self.symbol.upper():
            raise ValueError(f"Bar.symbol must be non-empty upper-case, got {self.symbol!r}")
        if min(self.open, self.high, self.low, self.close) <= 0:
            raise ValueError(f"Bar prices must be > 0: {self}")
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close):
            raise ValueError(f"Bar OHLC inconsistent: {self}")
        if self.volume < 0:
            raise ValueError(f"Bar.volume must be >= 0: {self}")
        if not 0 <= self.minute_of_day < 24 * 60:
            raise ValueError(f"Bar.minute_of_day out of range: {self.minute_of_day}")


class HaltState(str, enum.Enum):
    HALTED = "halted"
    RESUMED = "resumed"


@dataclass(frozen=True, slots=True)
class HaltEvent:
    symbol: str
    ts: datetime              # when the halt/resume took effect (UTC)
    state: HaltState
    reason_code: str = ""     # exchange reason code, e.g. "LUDP", "T1", "M"
    source: str = ""

    def __post_init__(self) -> None:
        _require_utc("HaltEvent.ts", self.ts)


class Side(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, enum.Enum):
    LIMIT = "limit"
    MARKET = "market"   # never used on thin names; adapters may reject it per policy
    STOP = "stop"


class TimeInForce(str, enum.Enum):
    DAY = "day"
    IOC = "ioc"
    GTC = "gtc"


class OrderStatus(str, enum.Enum):
    PENDING = "pending"        # accepted by us / broker, not yet working
    WORKING = "working"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELED = "canceled"
    REJECTED = "rejected"
    EXPIRED = "expired"

    @property
    def terminal(self) -> bool:
        return self in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """What the strategy/risk layer wants a broker to do.

    ``client_order_id`` is deterministic (see ``sgt.core.ids.client_order_id``) so a
    retried submission can never create a second order.
    """

    client_order_id: str
    account_id: str
    symbol: str
    side: Side
    qty: int
    order_type: OrderType
    limit_price: float | None
    tif: TimeInForce
    extended_hours: bool
    strategy_id: str
    reason: str                      # e.g. "entry", "exit:macd_fade", "exit:stop"
    created_at: datetime
    stop_price: float | None = None

    def __post_init__(self) -> None:
        _require_utc("OrderIntent.created_at", self.created_at)
        if self.qty <= 0:
            raise ValueError("OrderIntent.qty must be > 0")
        if self.order_type is OrderType.LIMIT and (self.limit_price is None or self.limit_price <= 0):
            raise ValueError("LIMIT orders require a positive limit_price")
        if self.order_type is OrderType.STOP and (self.stop_price is None or self.stop_price <= 0):
            raise ValueError("STOP orders require a positive stop_price")
        if self.extended_hours and self.order_type is not OrderType.LIMIT:
            raise ValueError("extended-hours orders must be LIMIT")


@dataclass(frozen=True, slots=True)
class OrderAck:
    client_order_id: str
    broker_order_id: str
    status: OrderStatus
    ts: datetime
    message: str = ""
    # Populated by every adapter for working orders so exposure views can count
    # open-order shares (2026-10-12, W4a review: router aggregate cap needs them).
    symbol: str = ""
    side: Side | None = None
    remaining_qty: int | None = None


@dataclass(frozen=True, slots=True)
class Fill:
    client_order_id: str
    broker_order_id: str
    account_id: str
    symbol: str
    side: Side
    qty: int
    price: float
    ts: datetime
    fill_id: str                     # unique per execution; used to dedupe replays
    fee: float = 0.0

    def __post_init__(self) -> None:
        _require_utc("Fill.ts", self.ts)
        if self.qty <= 0 or self.price <= 0:
            raise ValueError("Fill qty/price must be > 0")


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    qty: int                          # long-only system: qty >= 0
    avg_price: float


@dataclass(frozen=True, slots=True)
class AccountState:
    account_id: str
    cash: float
    settled_cash: float
    buying_power: float
    positions: tuple[Position, ...] = ()
    as_of: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class RiskVerdict(str, enum.Enum):
    APPROVE = "approve"
    REDUCE = "reduce"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    verdict: RiskVerdict
    qty: int                          # approved qty (0 when rejected)
    reasons: tuple[str, ...] = ()


class Mode(str, enum.Enum):
    BACKTEST = "backtest"
    SHADOW = "shadow"
    PAPER = "paper"
    LIVE = "live"
