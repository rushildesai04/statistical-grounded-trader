"""In-process simulated broker for the backtest engine and shadow mode.

Design rules (docs/IMPLEMENTATION_PLAN.md §8):
- No look-ahead: an order submitted while bar ``t`` is being processed first becomes
  eligible on bar ``t+1``. Fills reference the *next* bar's open.
- Conservative fills: a LIMIT fills at its limit (not a better intrabar price) unless
  the bar's open already crosses it; STOPs fill through gaps realistically.
- Participation cap: at most ``cost_model.participation_cap`` of a bar's volume fills
  per bar; the remainder stays working (PARTIALLY_FILLED).
- Halts: no fills while halted; after resume the next bar's open is the fill
  reference, so gap risk is realized.
- Cash accounting: buys reduce cash and settled cash immediately; sell proceeds settle
  after ``settle_days`` sessions. With ``cash_account=True`` buys exceeding settled
  cash are rejected.
- Determinism: fill ids and order ids derive only from the client order id, so the
  same inputs always produce byte-identical results.

The synchronous ``*_sync`` / ``on_bar`` / ``end_session`` methods are the backtest
engine's API. The async ``BrokerAdapter`` methods wrap the same state so SimBroker
also runs unchanged in shadow mode.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import AsyncIterator

from sgt.brokers.costs import CostModel
from sgt.core.interfaces import AdapterHealth, BrokerAdapter
from sgt.core.types import (
    AccountState,
    Bar,
    Fill,
    HaltEvent,
    HaltState,
    OrderAck,
    OrderIntent,
    OrderStatus,
    OrderType,
    Position,
    Segment,
    Side,
    TimeInForce,
)


@dataclass
class _Working:
    intent: OrderIntent
    submit_seq: int                 # per-symbol bars processed at submit time
    remaining: int
    filled_qty: int = 0
    status: OrderStatus = OrderStatus.PENDING
    fill_count: int = 0             # per-order fill index -> deterministic fill ids


@dataclass
class _Lot:
    qty: int = 0
    avg_price: float = 0.0


class SimBroker(BrokerAdapter):
    """Deterministic simulated brokerage account."""

    name = "sim"
    supports_extended_hours = True
    supports_broker_side_stops = True
    is_paper = True

    def __init__(
        self,
        account_id: str,
        cost_model: CostModel,
        starting_cash: float,
        *,
        settle_days: int = 1,
        cash_account: bool = False,
        calendar_sessions: Callable[[], list[date]] | None = None,
    ) -> None:
        if starting_cash < 0:
            raise ValueError("starting_cash must be >= 0")
        if settle_days < 0:
            raise ValueError("settle_days must be >= 0")
        self.account_id = account_id
        self.cost_model = cost_model
        self.cash = float(starting_cash)
        self.settled_cash = float(starting_cash)
        self.settle_days = settle_days
        self.cash_account = cash_account
        self._calendar = calendar_sessions
        self.realized_pnl = 0.0

        self._orders: dict[str, _Working] = {}
        self._acks: dict[str, OrderAck] = {}
        self._positions: dict[str, _Lot] = {}
        self._halted: set[str] = set()
        self._bar_seq: dict[str, int] = {}
        self._cur_session: date | None = None
        self._last_closed: date | None = None
        self._session_seq = 0
        # Pending sell settlements: (settle_at_session_seq, amount).
        self._pending_settle: list[tuple[int, float]] = []
        self._fills_log: list[Fill] = []
        self._fills_emitted: set[str] = set()

    # ------------------------------------------------------------------ sync API
    def submit_sync(self, intent: OrderIntent) -> OrderAck:
        """Accept an order. Idempotent on ``client_order_id``."""
        existing = self._acks.get(intent.client_order_id)
        if existing is not None:
            return existing

        if intent.account_id != self.account_id:
            return self._reject(intent, "account mismatch")
        if intent.extended_hours and intent.order_type is not OrderType.LIMIT:
            return self._reject(intent, "extended-hours orders must be LIMIT")
        if intent.side is Side.BUY and self.cash_account and intent.order_type is OrderType.LIMIT:
            assert intent.limit_price is not None
            need = intent.qty * intent.limit_price + self.cost_model.fees(intent.qty)
            if need > self.settled_cash + 1e-9:
                return self._reject(intent, "insufficient settled cash")

        seq = self._bar_seq.get(intent.symbol, 0)
        self._orders[intent.client_order_id] = _Working(
            intent=intent, submit_seq=seq, remaining=intent.qty, status=OrderStatus.PENDING
        )
        ack = OrderAck(
            client_order_id=intent.client_order_id,
            broker_order_id=self._broker_id(intent.client_order_id),
            status=OrderStatus.PENDING,
            ts=intent.created_at,
            message="accepted",
            symbol=intent.symbol,
            side=intent.side,
            remaining_qty=intent.qty,
        )
        self._acks[intent.client_order_id] = ack
        return ack

    def cancel_sync(self, client_order_id: str) -> OrderAck:
        wk = self._orders.get(client_order_id)
        if wk is None:
            return OrderAck(client_order_id, "", OrderStatus.REJECTED,
                            self._now(), "unknown order")
        if not wk.status.terminal:
            wk.status = OrderStatus.CANCELED
            wk.remaining = 0
        ack = OrderAck(client_order_id, self._broker_id(client_order_id),
                       OrderStatus.CANCELED, self._now(), "canceled",
                       symbol=wk.intent.symbol, side=wk.intent.side, remaining_qty=0)
        self._acks[client_order_id] = ack
        return ack

    def on_halt(self, event: HaltEvent) -> None:
        if event.state is HaltState.HALTED:
            self._halted.add(event.symbol)
        elif event.state is HaltState.RESUMED:
            self._halted.discard(event.symbol)

    def on_bar(self, bar: Bar) -> list[Fill]:
        """Process working orders for ``bar.symbol`` and return new fills."""
        sym = bar.symbol
        if self._cur_session is not None and bar.session_date != self._cur_session:
            self.end_session(self._cur_session)
        self._cur_session = bar.session_date

        seq_before = self._bar_seq.get(sym, 0)
        self._bar_seq[sym] = seq_before + 1
        if sym in self._halted:
            return []

        out: list[Fill] = []
        for wk in list(self._orders.values()):
            if wk.intent.symbol != sym or wk.status.terminal:
                continue
            if seq_before < wk.submit_seq:
                continue  # not eligible until the next bar (no same-bar fill)
            tradeable = bar.segment is Segment.RTH or wk.intent.extended_hours
            if not tradeable:
                continue  # ext-hours gating: do not fill, do not consume IOC life
            fill = self._try_fill(wk, bar)
            if fill is not None:
                out.append(fill)
            if wk.intent.tif is TimeInForce.IOC and not wk.status.terminal:
                wk.status = OrderStatus.FILLED if wk.remaining == 0 else OrderStatus.EXPIRED
                wk.remaining = 0
        self._fills_log.extend(out)
        return out

    def end_session(self, session_date: date) -> None:
        """Advance the settlement clock and expire DAY orders for a closed session.

        Settlement is measured in *trading sessions*. When a trading calendar is
        provided, the clock advances by the number of real sessions between the
        previously closed session and this one, so a gap over holidays/weekends (or
        sessions with no bars for a symbol) still settles correctly.

        Idempotent per session: closing a session that is already closed is a no-op, so
        the engine may call this explicitly at session end even though ``on_bar`` also
        auto-rolls on the next session's first bar — neither double-settles nor
        double-expires.
        """
        if self._last_closed is not None and session_date <= self._last_closed:
            return
        advance = self._sessions_elapsed(session_date)
        self._session_seq += advance
        self._last_closed = session_date
        released = 0.0
        remaining: list[tuple[int, float]] = []
        for settle_at, amount in self._pending_settle:
            if settle_at <= self._session_seq:
                released += amount
            else:
                remaining.append((settle_at, amount))
        self._pending_settle = remaining
        self.settled_cash += released
        for wk in self._orders.values():
            if wk.intent.tif is TimeInForce.DAY and not wk.status.terminal:
                wk.status = OrderStatus.EXPIRED
                wk.remaining = 0

    # ------------------------------------------------------------------ fills core
    def _try_fill(self, wk: _Working, bar: Bar) -> Fill | None:
        intent = wk.intent
        cap = self.cost_model.max_fill_qty(bar.volume)
        if cap <= 0:
            if wk.status is OrderStatus.PENDING:
                wk.status = OrderStatus.WORKING
            return None
        qty = min(wk.remaining, cap)
        slip = self.cost_model.slippage(bar.open, intent.side, qty, bar.volume)
        price = self._fill_price(intent, bar, slip)
        if price is None:
            if wk.status is OrderStatus.PENDING:
                wk.status = OrderStatus.WORKING
            return None

        if intent.side is Side.BUY:
            cost = qty * price + self.cost_model.fees(qty)
            if self.cash_account and cost > self.settled_cash + 1e-9:
                wk.status = OrderStatus.REJECTED
                wk.remaining = 0
                self._acks[intent.client_order_id] = OrderAck(
                    intent.client_order_id, self._broker_id(intent.client_order_id),
                    OrderStatus.REJECTED, bar.ts, "insufficient settled cash at fill",
                    symbol=intent.symbol, side=intent.side, remaining_qty=0)
                return None
        else:  # SELL: never fill more than the held position
            held = self._positions.get(intent.symbol, _Lot()).qty
            qty = min(qty, held)
            if qty <= 0:
                wk.status = OrderStatus.REJECTED
                wk.remaining = 0
                self._acks[intent.client_order_id] = OrderAck(
                    intent.client_order_id, self._broker_id(intent.client_order_id),
                    OrderStatus.REJECTED, bar.ts, "no position to sell",
                    symbol=intent.symbol, side=intent.side, remaining_qty=0)
                return None

        fee = self.cost_model.fees(qty)
        self._apply_fill(intent, qty, price, fee)
        wk.remaining -= qty
        wk.filled_qty += qty
        fill_id = f"{intent.client_order_id}-f{wk.fill_count}"
        wk.fill_count += 1
        wk.status = OrderStatus.FILLED if wk.remaining == 0 else OrderStatus.PARTIALLY_FILLED
        return Fill(
            client_order_id=intent.client_order_id,
            broker_order_id=self._broker_id(intent.client_order_id),
            account_id=self.account_id,
            symbol=intent.symbol,
            side=intent.side,
            qty=qty,
            price=round(price, 6),
            ts=bar.ts,
            fill_id=fill_id,
            fee=round(fee, 6),
        )

    def _fill_price(self, intent: OrderIntent, bar: Bar, slip: float) -> float | None:
        """Return the fill price or None if the order stays working this bar."""
        side, otype = intent.side, intent.order_type
        if otype is OrderType.MARKET:
            return bar.open + slip if side is Side.BUY else bar.open - slip
        if otype is OrderType.LIMIT:
            lim = intent.limit_price
            assert lim is not None
            if side is Side.BUY:
                if bar.open + slip <= lim:
                    return min(lim, bar.open + slip)
                if bar.low + slip <= lim:
                    return lim
                return None
            else:
                if bar.open - slip >= lim:
                    return max(lim, bar.open - slip)
                if bar.high - slip >= lim:
                    return lim
                return None
        # STOP
        stop = intent.stop_price
        assert stop is not None
        if side is Side.SELL:
            if bar.low <= stop:
                return max(0.000001, min(bar.open, stop) - slip)
            return None
        else:  # protective buy stop (symmetric; completeness)
            if bar.high >= stop:
                return max(bar.open, stop) + slip
            return None

    def _apply_fill(self, intent: OrderIntent, qty: int, price: float, fee: float) -> None:
        lot = self._positions.setdefault(intent.symbol, _Lot())
        if intent.side is Side.BUY:
            self.cash -= qty * price + fee
            self.settled_cash -= qty * price + fee
            new_qty = lot.qty + qty
            lot.avg_price = (lot.qty * lot.avg_price + qty * price) / new_qty
            lot.qty = new_qty
        else:
            proceeds = qty * price - fee
            self.cash += proceeds
            self.realized_pnl += qty * (price - lot.avg_price) - fee
            lot.qty -= qty
            if lot.qty == 0:
                lot.avg_price = 0.0
            self._pending_settle.append((self._session_seq + self.settle_days, proceeds))

    # ------------------------------------------------------------------ helpers
    def _reject(self, intent: OrderIntent, msg: str) -> OrderAck:
        ack = OrderAck(intent.client_order_id, "", OrderStatus.REJECTED,
                       intent.created_at, msg,
                       symbol=intent.symbol, side=intent.side, remaining_qty=0)
        self._acks[intent.client_order_id] = ack
        self._orders[intent.client_order_id] = _Working(
            intent=intent, submit_seq=self._bar_seq.get(intent.symbol, 0),
            remaining=0, status=OrderStatus.REJECTED)
        return ack

    @staticmethod
    def _broker_id(client_order_id: str) -> str:
        return "sim-" + client_order_id

    def _now(self) -> datetime:
        from datetime import timezone
        return datetime.now(timezone.utc)

    def _sessions_elapsed(self, closed: date) -> int:
        """Trading sessions between the previously closed session and ``closed``."""
        if self._last_closed is None or self._calendar is None:
            return 1
        cal = self._calendar()
        count = sum(1 for d in cal if self._last_closed < d <= closed)
        return max(1, count)

    def order_state_sync(self, client_order_id: str) -> OrderAck | None:
        """Current state of one order (None if unknown). Public read used by the
        backtest executor to surface terminal states to the order lifecycle."""
        wk = self._orders.get(client_order_id)
        return None if wk is None else self._ack_for(wk)

    def _ack_for(self, wk: _Working) -> OrderAck:
        cid = wk.intent.client_order_id
        return OrderAck(cid, self._broker_id(cid), wk.status,
                        self._acks[cid].ts if cid in self._acks else wk.intent.created_at,
                        wk.status.value,
                        symbol=wk.intent.symbol, side=wk.intent.side,
                        remaining_qty=wk.remaining)

    # ------------------------------------------------------------------ async adapter
    async def connect(self) -> None:
        return None

    async def account_state(self) -> AccountState:
        positions = tuple(
            Position(sym, lot.qty, round(lot.avg_price, 6))
            for sym, lot in sorted(self._positions.items()) if lot.qty != 0
        )
        return AccountState(
            account_id=self.account_id,
            cash=round(self.cash, 6),
            settled_cash=round(self.settled_cash, 6),
            buying_power=round(self.settled_cash if self.cash_account else self.cash, 6),
            positions=positions,
        )

    async def submit(self, intent: OrderIntent) -> OrderAck:
        return self.submit_sync(intent)

    async def cancel(self, client_order_id: str) -> OrderAck:
        return self.cancel_sync(client_order_id)

    async def open_orders(self) -> list[OrderAck]:
        return [self._ack_for(wk) for wk in self._orders.values() if not wk.status.terminal]

    async def fills(self) -> AsyncIterator[Fill]:
        for fl in list(self._fills_log):
            if fl.fill_id in self._fills_emitted:
                continue
            self._fills_emitted.add(fl.fill_id)
            yield fl

    async def health(self) -> AdapterHealth:
        return AdapterHealth(ok=True, detail="simulated")

    async def close(self) -> None:
        return None
