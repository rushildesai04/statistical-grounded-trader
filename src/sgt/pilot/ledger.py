"""Real-money pilot ledger: caps, own positions and order book in the ops DB (red-team F-6/F-4/F-5/F-8, 2026-11-05).

One transaction (``BEGIN IMMEDIATE``) checks every cap and inserts the opening order row, so the fade listener and
the cron jobs share one budget and two processes can never both pass the check (F-6). Exposure = the pilot's own
positions at live marks + the unfilled part of every working opening order at its limit + this order. A missing or
bad mark for the symbol being opened, a kill/pause flag or any cap breach refuses the order (fail closed); other
holdings without a live mark count at cost x UNPRICED.

Positions are signed (short < 0) and hold only what the pilot itself traded, so FLATTEN and covers can never touch
the owner's other holdings (F-5); closing orders are capped at the pilot's own quantity (F-8; the executor also caps
at the broker's quantity). Fills are applied from the order's cumulative filled quantity, so replaying a fill report
is a no-op. Design: docs/research/live_pilot_schwab_2026-11-05.md.
"""
from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from sgt.ops.store import OpsStore, iso, utcnow

HARD_CAP = 10_000.0
PER_ORDER = 2_000.0
SUBCAPS = {"fade": 4_000.0, "6040": 4_000.0, "tom": 2_000.0}
KILL_FLAG = "pilot_kill"
UNPRICED = 1.2                        # held position with no live mark is valued at cost x this
# side -> (signed share direction, opens exposure)
SIDES = {"BUY": (1, True), "SELL_SHORT": (-1, True), "SELL": (-1, False), "BUY_TO_COVER": (1, False)}
OPEN_STATES = ("reserved", "working")
TERMINAL = ("filled", "canceled", "rejected", "expired")
_ID_PART = re.compile(r"[A-Za-z0-9_.-]{1,24}")

SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_orders(
  client_order_id TEXT PRIMARY KEY, strategy TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
  qty INTEGER NOT NULL, limit_price REAL NOT NULL, order_type TEXT NOT NULL, opening INTEGER NOT NULL,
  status TEXT NOT NULL,
  filled_qty INTEGER NOT NULL DEFAULT 0, avg_price REAL, broker_order_id TEXT, reason TEXT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_pilot_orders_status ON pilot_orders(status, strategy);
CREATE TABLE IF NOT EXISTS pilot_positions(
  strategy TEXT NOT NULL, symbol TEXT NOT NULL, qty INTEGER NOT NULL, cost REAL NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(strategy, symbol));
"""


class CapRefused(Exception):
    """An order the pilot must not send; ``str(e)`` is the plain-words reason for the owner's text."""


@dataclass(frozen=True)
class Position:
    strategy: str
    symbol: str
    qty: int          # signed: short < 0
    cost: float       # average entry price


def order_id(strategy: str, decision: str, leg: str) -> str:
    """Deterministic client order id per (strategy, decision, leg), e.g. ``pilot:fade:812:entry`` (F-11).

    A restart re-derives the same id, so the ledger's primary key and the adapter's idempotency ledger both refuse
    a second POST. Each escalation step of an exit is its own leg (``cover1``, ``cover2``, ``covermkt``)."""
    for part in (strategy, decision, leg):
        if not _ID_PART.fullmatch(part):
            raise ValueError(f"bad order id part {part!r}")
    return f"pilot:{strategy}:{decision}:{leg}"


def init(db: OpsStore) -> None:
    with db.tx() as c:
        for stmt in filter(str.strip, SCHEMA.split(";")):
            c.execute(stmt)


def _mark(marks: dict[str, float], symbol: str) -> float:
    m = marks.get(symbol)
    if m is None or not math.isfinite(m) or m <= 0:
        raise CapRefused(f"no valid price for {symbol}; not trading blind")
    return float(m)


def _held_value(r: sqlite3.Row, marks: dict[str, float]) -> float:
    """Live mark when there is one; otherwise cost x UNPRICED (the fade stop level, a buffer for longs), so a quote
    outage on one holding never blocks every other strategy (review 2026-11-05 F-A) and never under-counts."""
    m = marks.get(r["symbol"])
    px = m if m is not None and math.isfinite(m) and m > 0 else r["cost"] * UNPRICED
    return abs(r["qty"]) * px


def _exposure(c: sqlite3.Connection, marks: dict[str, float], strategy: str | None = None) -> float:
    where, args = ("WHERE strategy=?", (strategy,)) if strategy else ("", ())
    held = sum(_held_value(r, marks)
               for r in c.execute(f"SELECT symbol, qty, cost FROM pilot_positions {where}", args))  # noqa: S608
    q = "SELECT qty, filled_qty, limit_price FROM pilot_orders WHERE opening=1 AND status IN ('reserved','working')"
    working = sum((r["qty"] - r["filled_qty"]) * r["limit_price"]
                  for r in c.execute(q + (" AND strategy=?" if strategy else ""), args))
    return held + working


def reserve(db: OpsStore, *, client_order_id: str, strategy: str, symbol: str, side: str, qty: int,
            limit_price: float, marks: dict[str, float], order_type: str = "limit",
            now: datetime | None = None) -> None:
    """Atomically check every cap and record the order as ``reserved``. Raises CapRefused; never sends anything.

    Opening orders (BUY, SELL_SHORT) must fit the hard cap, the per-order cap and the strategy sub-cap. Closing
    orders (SELL, BUY_TO_COVER) are never cap-limited but must not exceed the pilot's own open quantity, so an exit
    can never touch shares the pilot does not own or flip a position. ``limit_price`` is the limit, the stop
    trigger (``order_type="stop"``) or the reference mark (``"market"``, closing orders only)."""
    now = now or utcnow()
    if side not in SIDES:
        raise CapRefused(f"side {side!r} not allowed")
    if strategy not in SUBCAPS:
        raise CapRefused(f"strategy {strategy!r} has no real-money budget")
    if not isinstance(qty, int) or qty <= 0 or not math.isfinite(limit_price) or limit_price <= 0:
        raise CapRefused("bad quantity or price")
    direction, opening = SIDES[side]
    if order_type not in ("limit", "stop", "market") or (opening and order_type != "limit"):
        raise CapRefused(f"{order_type} orders cannot open a position")
    with db.tx() as c:
        if c.execute("SELECT 1 FROM pilot_orders WHERE client_order_id=?", (client_order_id,)).fetchone():
            raise CapRefused(f"order {client_order_id} already exists")
        pos = c.execute("SELECT qty FROM pilot_positions WHERE strategy=? AND symbol=?", (strategy, symbol)).fetchone()
        held = pos["qty"] if pos else 0
        if opening:
            flags = {r["key"]: r["value"] for r in c.execute("SELECT key, value FROM flags WHERE key IN (?, 'paused')",
                                                             (KILL_FLAG,))}
            if flags.get(KILL_FLAG) == "1" or flags.get("paused") == "1":
                raise CapRefused("trading is paused")
            if held * direction < 0:
                raise CapRefused(f"{side} would trade against the pilot's own {symbol} position")
            _mark(marks, symbol)               # the symbol being opened always needs a live price
            notional = qty * limit_price
            if notional > PER_ORDER + 1e-9:
                raise CapRefused(f"${notional:,.0f} is over the ${PER_ORDER:,.0f} per-order cap")
            strat = _exposure(c, marks, strategy)
            if strat + notional > SUBCAPS[strategy] + 1e-9:
                raise CapRefused(f"{strategy} budget: ${strat:,.0f} in use + ${notional:,.0f} > "
                                 f"${SUBCAPS[strategy]:,.0f}")
            total = _exposure(c, marks)
            if total + notional > HARD_CAP + 1e-9:
                raise CapRefused(f"total: ${total:,.0f} in use + ${notional:,.0f} > ${HARD_CAP:,.0f} cap")
        else:
            pending = c.execute("SELECT COALESCE(SUM(qty - filled_qty), 0) FROM pilot_orders WHERE strategy=? AND "
                                "symbol=? AND side=? AND status IN ('reserved','working')",
                                (strategy, symbol, side)).fetchone()[0]
            own = -held * direction                # shares the pilot holds on the side this order closes
            if qty + pending > own:
                raise CapRefused(f"{side} {qty} {symbol} exceeds the pilot's own {own} (minus {pending} working)")
        c.execute("INSERT INTO pilot_orders(client_order_id, strategy, symbol, side, qty, limit_price, order_type, "
                  "opening, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,'reserved',?,?)",
                  (client_order_id, strategy, symbol, side, qty, limit_price, order_type, int(opening), iso(now),
                   iso(now)))


def working(db: OpsStore, client_order_id: str, broker_order_id: str, now: datetime | None = None) -> None:
    now = now or utcnow()
    with db.tx() as c:
        c.execute("UPDATE pilot_orders SET status='working', broker_order_id=?, updated_at=? WHERE client_order_id=? "
                  "AND status='reserved'", (broker_order_id, iso(now), client_order_id))


def fill(db: OpsStore, client_order_id: str, cum_qty: int, avg_price: float, now: datetime | None = None) -> int:
    """Apply a fill report by cumulative quantity; returns the newly applied shares (0 for a replay)."""
    now = now or utcnow()
    with db.tx() as c:
        o = c.execute("SELECT * FROM pilot_orders WHERE client_order_id=?", (client_order_id,)).fetchone()
        if o is None:
            raise KeyError(client_order_id)
        if cum_qty > o["qty"] or not math.isfinite(avg_price) or avg_price <= 0:
            raise ValueError(f"bad fill for {client_order_id}: {cum_qty} @ {avg_price}")
        delta = cum_qty - o["filled_qty"]
        if delta <= 0:
            return 0
        # price of just the new shares, from the change in cumulative cost
        px = (cum_qty * avg_price - o["filled_qty"] * (o["avg_price"] or 0.0)) / delta
        signed = SIDES[o["side"]][0] * delta
        p = c.execute("SELECT qty, cost FROM pilot_positions WHERE strategy=? AND symbol=?",
                      (o["strategy"], o["symbol"])).fetchone()
        old, cost = (p["qty"], p["cost"]) if p else (0, 0.0)
        new = old + signed
        if old == 0 or (old > 0) == (signed > 0):
            cost = (abs(old) * cost + delta * px) / abs(new)       # adding to the position: blend the entry
        elif new * old < 0:
            raise ValueError(f"fill {client_order_id} would flip the pilot's {o['symbol']} position")
        c.execute("INSERT INTO pilot_positions(strategy, symbol, qty, cost, updated_at) VALUES (?,?,?,?,?) "
                  "ON CONFLICT(strategy, symbol) DO UPDATE SET qty=excluded.qty, cost=excluded.cost, "
                  "updated_at=excluded.updated_at", (o["strategy"], o["symbol"], new, cost if new else 0.0, iso(now)))
        status = "filled" if cum_qty == o["qty"] else o["status"]
        c.execute("UPDATE pilot_orders SET filled_qty=?, avg_price=?, status=?, updated_at=? WHERE client_order_id=?",
                  (cum_qty, avg_price, status, iso(now), client_order_id))
        return delta


def close(db: OpsStore, client_order_id: str, status: str, reason: str = "", now: datetime | None = None) -> None:
    """Terminal state (canceled / rejected / expired); releases whatever was not filled."""
    if status not in TERMINAL or status == "filled":
        raise ValueError(status)
    now = now or utcnow()
    with db.tx() as c:
        c.execute("UPDATE pilot_orders SET status=?, reason=?, updated_at=? WHERE client_order_id=? AND status IN "
                  "('reserved','working')", (status, reason[:500], iso(now), client_order_id))


def get(db: OpsStore, client_order_id: str) -> sqlite3.Row | None:
    with db.tx() as c:
        return c.execute("SELECT * FROM pilot_orders WHERE client_order_id=?", (client_order_id,)).fetchone()


def positions(db: OpsStore, strategy: str | None = None) -> list[Position]:
    where, args = ("AND strategy=?", (strategy,)) if strategy else ("", ())
    with db.tx() as c:
        rows = c.execute(f"SELECT strategy, symbol, qty, cost FROM pilot_positions WHERE qty != 0 {where} "  # noqa: S608
                         "ORDER BY strategy, symbol", args).fetchall()
    return [Position(r["strategy"], r["symbol"], r["qty"], r["cost"]) for r in rows]


def open_orders(db: OpsStore) -> list[sqlite3.Row]:
    with db.tx() as c:
        return c.execute("SELECT * FROM pilot_orders WHERE status IN ('reserved','working') ORDER BY created_at"
                         ).fetchall()


def exposure(db: OpsStore, marks: dict[str, float], strategy: str | None = None) -> float:
    with db.tx() as c:
        return _exposure(c, marks, strategy)


def kill(db: OpsStore, on: bool = True) -> None:
    db.set_flag(KILL_FLAG, "1" if on else "0")
