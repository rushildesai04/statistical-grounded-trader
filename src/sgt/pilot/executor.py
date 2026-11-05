"""Real-money pilot executor at Schwab (design docs/research/live_pilot_schwab_2026-11-05.md).

Every order goes ledger first, broker second: ``ledger.reserve`` (caps, own-quantity limits, kill flag) in one
transaction, then ``broker.submit`` (adapter idempotency ledger + short guards). The intent is rebuilt from the
ledger row, so a retry after a crash sends byte-identical orders and the adapter dedupes it. An ambiguous submit
keeps the budget reserved until ``sync`` resolves it from the broker (fail closed). Exits are never cap-limited and
are never blocked by PAUSE or the kill flag.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Protocol

from sgt.core.interfaces import BrokerRejectedError
from sgt.core.types import OrderAck, OrderIntent, OrderStatus, OrderType, Side, TimeInForce
from sgt.ops.store import OpsStore, utcnow
from sgt.pilot import ledger
from sgt.pilot.ledger import CapRefused

log = logging.getLogger(__name__)

LONG_ALLOW = frozenset({"SPY", "IEF"})
STRATEGY_SIDES = {"6040": {"BUY", "SELL"}, "tom": {"BUY", "SELL"}, "fade": {"SELL_SHORT", "BUY_TO_COVER"}}
MAX_FROM_MARK = 0.02          # an opening limit more than 2% from the live mark is refused (bad quote / fat finger)
UNSENT_AFTER = timedelta(minutes=2)
_TERMINAL = {OrderStatus.CANCELED: "canceled", OrderStatus.REJECTED: "rejected", OrderStatus.EXPIRED: "expired"}


class Broker(Protocol):
    account_id: str

    async def submit(self, intent: OrderIntent) -> OrderAck: ...
    async def cancel(self, client_order_id: str) -> OrderAck: ...
    async def order_snapshot(self, broker_order_id: str, cid: str) -> tuple[OrderAck, int, float | None]: ...
    async def order_snapshots(self) -> dict[str, tuple[OrderAck, int, float | None]]: ...
    async def signed_positions(self) -> dict[str, int]: ...


def tick(px: float, up: bool) -> float:
    """Round to a valid US equity increment (1c at/above $1, 0.01c below), toward the marketable side."""
    if not math.isfinite(px) or px <= 0:
        raise ValueError(f"bad price {px}")
    step = 0.01 if px >= 1.0 else 0.0001
    n = px / step
    n = math.ceil(n - 1e-9) if up else math.floor(n + 1e-9)
    return round(max(n, 1) * step, 4)


def _adapter_lookup(cid: str) -> tuple[str, str | None, dict[str, Any]] | None:
    from sgt.brokers.schwab_ledger import ledger_lookup
    return ledger_lookup(cid)


class Pilot:
    def __init__(self, db: OpsStore, broker: Broker, *, clock: Callable[[], datetime] = utcnow,
                 adapter_lookup: Callable[[str], Any] = _adapter_lookup) -> None:
        self.db, self.broker, self.clock, self._lookup = db, broker, clock, adapter_lookup
        ledger.init(db)

    # ------------------------------------------------------------------ orders
    async def place(self, *, strategy: str, decision: str, leg: str, symbol: str, side: str, qty: int,
                    marks: dict[str, float], limit_price: float | None = None,
                    stop_price: float | None = None) -> str:
        """Reserve then submit one order; returns its client order id. Raises CapRefused / BrokerError.

        Idempotent per (strategy, decision, leg): calling again after a crash re-sends the SAME order (the adapter
        dedupes it), and calling again after it finished does nothing."""
        cid = ledger.order_id(strategy, decision, leg)
        if side not in STRATEGY_SIDES.get(strategy, ()):
            raise CapRefused(f"{strategy} may not {side}")
        opening = ledger.SIDES[side][1] if side in ledger.SIDES else False
        if opening and side == "BUY" and symbol not in LONG_ALLOW:
            raise CapRefused(f"{symbol} is not on the long allowlist")
        if stop_price is not None and limit_price is not None:
            raise CapRefused("stop-limit orders are not used")
        order_type = "stop" if stop_price is not None else "limit" if limit_price is not None else "market"
        if opening:
            if limit_price is None:
                raise CapRefused("entries must be limit orders")
            mark = marks.get(symbol)
            if mark is None or not math.isfinite(mark) or mark <= 0 or abs(limit_price / mark - 1) > MAX_FROM_MARK:
                raise CapRefused(f"{symbol} limit {limit_price} too far from last {mark}")
        row = ledger.get(self.db, cid)
        if row is None:
            ref = stop_price if stop_price is not None else limit_price
            if ref is None:                                    # market close: reference only, never a cap input
                ref = marks.get(symbol)
                if ref is None or not math.isfinite(ref) or ref <= 0:
                    ref = next((p.cost for p in ledger.positions(self.db, strategy) if p.symbol == symbol), 0.0)
            ledger.reserve(self.db, client_order_id=cid, strategy=strategy, symbol=symbol, side=side, qty=qty,
                           limit_price=float(ref), marks=marks, order_type=order_type, now=self.clock())
            row = ledger.get(self.db, cid)
        elif row["status"] not in ledger.OPEN_STATES or row["broker_order_id"]:
            return cid                                         # finished, or already at the broker
        await self._submit(row)
        return cid

    def _intent(self, row: Any) -> OrderIntent:
        side = row["side"]
        typ = {"limit": OrderType.LIMIT, "stop": OrderType.STOP, "market": OrderType.MARKET}[row["order_type"]]
        return OrderIntent(
            client_order_id=row["client_order_id"], account_id=self.broker.account_id, symbol=row["symbol"],
            side=Side.BUY if side in ("BUY", "BUY_TO_COVER") else Side.SELL, qty=int(row["qty"]), order_type=typ,
            limit_price=row["limit_price"] if typ is OrderType.LIMIT else None, tif=TimeInForce.DAY,
            extended_hours=False, strategy_id=f"pilot-{row['strategy']}", reason=row["client_order_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            stop_price=row["limit_price"] if typ is OrderType.STOP else None,
            short=side in ("SELL_SHORT", "BUY_TO_COVER"))

    async def _submit(self, row: Any) -> None:
        cid = row["client_order_id"]
        if row["opening"] and (self.db.flag(ledger.KILL_FLAG) == "1" or self.db.paused()):
            # review L3: a FLATTEN/PAUSE from another process between reserve and POST still wins
            ledger.close(self.db, cid, "canceled", "paused before send", now=self.clock())
            raise CapRefused("trading is paused")
        try:
            ack = await self.broker.submit(self._intent(row))
        except BrokerRejectedError as e:
            ledger.close(self.db, cid, "rejected", str(e), now=self.clock())
            self.db.audit("pilot_rejected", cid=cid, reason=str(e)[:300])
            raise
        # BrokerUnavailableError propagates: the row stays 'reserved' (budget held) until sync() resolves it.
        ledger.working(self.db, cid, ack.broker_order_id, now=self.clock())
        self.db.audit("pilot_submitted", cid=cid, broker_order_id=ack.broker_order_id)
        if ack.status in _TERMINAL:
            ledger.close(self.db, cid, _TERMINAL[ack.status], ack.message, now=self.clock())

    async def cancel(self, cid: str) -> None:
        row = ledger.get(self.db, cid)
        if row is None or row["status"] not in ledger.OPEN_STATES or not row["broker_order_id"]:
            return
        await self.broker.cancel(cid)
        await self._sync_one(ledger.get(self.db, cid))

    # ------------------------------------------------------------------ broker truth
    async def sync(self) -> list[str]:
        """Bring every open pilot order up to date from the broker; returns notes for the owner."""
        notes: list[str] = []
        rows = ledger.open_orders(self.db)
        snaps = await self.broker.order_snapshots() if any(r["broker_order_id"] for r in rows) else {}
        for row in rows:
            note = await self._sync_one(row, snaps)
            if note:
                notes.append(note)
        return notes

    async def _sync_one(self, row: Any, snaps: dict | None = None) -> str | None:
        cid, now = row["client_order_id"], self.clock()
        bid = row["broker_order_id"]
        if not bid:
            prior = self._lookup(cid)
            if prior is not None and prior[1]:
                bid = prior[1]
                ledger.working(self.db, cid, bid, now=now)
            elif prior is None and now - datetime.fromisoformat(row["created_at"]) > UNSENT_AFTER:
                ledger.close(self.db, cid, "expired", "never sent", now=now)    # crashed before the POST
                return None
            elif prior is None:
                return None                                    # another process is sending it right now
            else:
                return f"{row['symbol']} order {cid}: outcome unknown at Schwab; its budget stays held"
        snap = (snaps or {}).get(bid)
        ack, cum, avg = snap if snap is not None else await self.broker.order_snapshot(bid, cid)
        if cum and avg:
            ledger.fill(self.db, cid, min(cum, int(row["qty"])), avg, now=now)
        if ack.status in _TERMINAL:
            ledger.close(self.db, cid, _TERMINAL[ack.status], ack.message, now=now)
        return None

    async def reconcile(self) -> list[str]:
        """Pilot positions must be covered by the broker's (same sign, at least as large). A mismatch seen twice,
        with a sync in between, sets the kill flag: entries stop, exits continue, owner is told."""
        problems: list[str] = []
        for attempt in range(2):
            await self.sync()
            broker = await self.broker.signed_positions()
            own: dict[str, int] = {}
            for p in ledger.positions(self.db):
                own[p.symbol] = own.get(p.symbol, 0) + p.qty
            problems = [f"{s}: pilot {q:+d}, Schwab {broker.get(s, 0):+d}" for s, q in sorted(own.items())
                        if (q > 0 and broker.get(s, 0) < q) or (q < 0 and broker.get(s, 0) > q)]
            if not problems:
                return []
        ledger.kill(self.db)
        self.db.audit("pilot_reconcile_mismatch", problems=problems)
        return problems

    async def flatten(self, marks: dict[str, float], through: float = 0.005) -> list[str]:
        """Close every pilot position (never the owner's other holdings) with marketable limits. Sets the kill flag
        first, cancels the pilot's working orders (including protective stops), then sends one close per
        position. Returns plain-words lines; positions are verified by a later ``reconcile``."""
        ledger.kill(self.db)
        for row in ledger.open_orders(self.db):
            await self.cancel(row["client_order_id"])
        await self.sync()
        stamp = self.clock().strftime("flat%Y%m%d%H%M%S")
        out: list[str] = []
        for i, p in enumerate(ledger.positions(self.db)):
            mark = marks.get(p.symbol)
            if mark is None or not math.isfinite(mark) or mark <= 0:
                out.append(f"{p.symbol}: no price, NOT closed; close it in the Schwab app")
                continue
            side = "SELL" if p.qty > 0 else "BUY_TO_COVER"
            px = tick(mark * (1 - through) if p.qty > 0 else mark * (1 + through), up=p.qty < 0)
            try:
                await self.place(strategy=p.strategy, decision=stamp, leg=str(i), symbol=p.symbol, side=side,
                                 qty=abs(p.qty), marks=marks, limit_price=px)
                out.append(f"{p.symbol}: {side} {abs(p.qty)} @ {px} sent")
            except Exception as e:  # noqa: BLE001 - one failed close must not stop the others; detail logged only
                log.exception("flatten %s failed", p.symbol)        # review L4: broker bodies never go into texts
                out.append(f"{p.symbol}: close FAILED ({type(e).__name__}); close it in the Schwab app")
        self.db.audit("pilot_flatten", lines=out)
        return out
