"""Pure decision rules: signal + current state -> order plan or a skip reason.

No I/O here; the runner supplies time, last price and state. Every skip carries a
reason so the journal and notifications explain each decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from sgt.signals_live.ideas import StockIdea

ET = ZoneInfo("America/New_York")

# NVB alert types traded with the pre-registered rule (prereg_telegram_alerts A1 cells).
ALERT_TYPES = frozenset({"volume_increasing", "volume_spike", "gaining_volume", "gaining_momentum",
                         "new_high", "intraday_high", "testing_high", "price_level", "resumed", "news"})


@dataclass(frozen=True)
class Limits:
    per_trade_usd: float = 2_000.0
    max_open: int = 5
    daily_loss_stop_usd: float = 1_000.0
    max_signal_age_s: float = 60.0
    alert_reaction_s: float = 10.0
    alert_hold_min: int = 15
    alert_window: tuple[time, time] = (time(9, 30), time(15, 44))
    idea_window: tuple[time, time] = (time(4, 0), time(15, 30))
    price_min: float = 1.0
    price_max: float = 50.0
    idea_entry_slip: float = 0.005      # limit = ideal entry * (1 + slip)
    idea_max_runaway: float = 0.03      # skip if last trade already > entry * (1 + this)
    idea_fill_timeout_min: int = 10
    flatten_at: time = time(15, 55)


@dataclass
class State:
    paused: bool = False
    pause_reason: str = ""
    open_symbols: set[str] = field(default_factory=set)      # held or entry pending
    done_today: set[tuple[str, str, str]] = field(default_factory=set)  # (kind, key, symbol)
    day: str = ""

    def roll(self, now: datetime) -> None:
        d = now.astimezone(ET).date().isoformat()
        if d != self.day:
            self.day, self.done_today = d, set()


@dataclass(frozen=True)
class Skip:
    reason: str


@dataclass(frozen=True)
class AlertPlan:
    symbol: str
    alert_type: str
    qty: int
    submit_at: datetime          # first minute boundary >= posted + reaction
    hold: timedelta


@dataclass(frozen=True)
class IdeaLeg:
    qty: int
    take_profit: float
    stop: float


@dataclass(frozen=True)
class IdeaPlan:
    symbol: str
    limit: float
    legs: tuple[IdeaLeg, ...]
    cancel_after: timedelta


def round_px(p: float) -> float:
    """Alpaca tick rules: 2 decimals at >= $1, 4 below."""
    return round(p, 2) if p >= 1.0 else round(p, 4)


def _common(symbol: str, posted: datetime, now: datetime, state: State, lim: Limits) -> Skip | None:
    if state.paused:
        return Skip(f"paused: {state.pause_reason}")
    if (now - posted).total_seconds() > lim.max_signal_age_s:
        return Skip("stale signal")
    if symbol in state.open_symbols:
        return Skip("already holding/entering symbol")
    if len(state.open_symbols) >= lim.max_open:
        return Skip(f"max open positions ({lim.max_open})")
    return None


def decide_alert(symbol: str, alert_type: str, posted: datetime, now: datetime, last_price: float | None,
                 state: State, lim: Limits = Limits()) -> AlertPlan | Skip:
    state.roll(now)
    if alert_type not in ALERT_TYPES:
        return Skip(f"type {alert_type} not traded")
    if (s := _common(symbol, posted, now, state, lim)) is not None:
        return s
    if ("alert", alert_type, symbol) in state.done_today:
        return Skip("already traded this type today")
    submit = posted + timedelta(seconds=lim.alert_reaction_s)
    submit = submit.replace(second=0, microsecond=0) + (timedelta(minutes=1) if submit.second or submit.microsecond else timedelta(0))
    t = submit.astimezone(ET).time()
    if not (lim.alert_window[0] <= t <= lim.alert_window[1]):
        return Skip("outside 09:30-15:44 ET entry window")
    if last_price is None or not (lim.price_min <= last_price <= lim.price_max):
        return Skip(f"price {last_price} outside ${lim.price_min:g}-{lim.price_max:g}")
    qty = math.floor(lim.per_trade_usd / last_price)
    if qty < 1:
        return Skip("size < 1 share")
    return AlertPlan(symbol, alert_type, qty, submit, timedelta(minutes=lim.alert_hold_min))


def decide_idea(idea: StockIdea, posted: datetime, now: datetime, last_price: float | None,
                state: State, lim: Limits = Limits()) -> IdeaPlan | Skip:
    state.roll(now)
    if (s := _common(idea.symbol, posted, now, state, lim)) is not None:
        return s
    if ("idea", "stock", idea.symbol) in state.done_today:
        return Skip("already traded this idea today")
    t = posted.astimezone(ET).time()
    if not (lim.idea_window[0] <= t <= lim.idea_window[1]):
        return Skip("outside 04:00-15:30 ET idea window")
    if not (lim.price_min <= idea.entry <= lim.price_max):
        return Skip(f"entry {idea.entry} outside ${lim.price_min:g}-{lim.price_max:g}")
    if last_price is not None and last_price > idea.entry * (1 + lim.idea_max_runaway):
        return Skip(f"already ran: last {last_price} > entry {idea.entry} +{lim.idea_max_runaway:.0%}")
    limit = round_px(idea.entry * (1 + lim.idea_entry_slip))
    total = math.floor(lim.per_trade_usd / limit)
    n = len(idea.targets_pct)
    if total < n:
        return Skip("size too small to split across targets")
    base, extra = divmod(total, n)
    stop = round_px(min(idea.stop, limit - 0.01))
    legs = tuple(IdeaLeg(base + (1 if i < extra else 0), round_px(max(idea.entry * (1 + tp), limit + 0.01)), stop)
                 for i, tp in enumerate(idea.targets_pct))
    return IdeaPlan(idea.symbol, limit, legs, timedelta(minutes=lim.idea_fill_timeout_min))
