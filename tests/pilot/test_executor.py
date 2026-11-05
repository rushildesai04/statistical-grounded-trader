from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sgt.core.interfaces import BrokerRejectedError, BrokerUnavailableError
from sgt.core.types import OrderAck, OrderStatus, OrderType, Side
from sgt.ops.store import OpsStore
from sgt.pilot import ledger
from sgt.pilot.executor import Pilot, tick
from sgt.pilot.ledger import CapRefused

T0 = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
MARKS = {"SPY": 500.0, "IEF": 100.0, "ABCD": 10.0}


class FakeBroker:
    account_id = "acct"

    def __init__(self) -> None:
        self.sent: list = []
        self.orders: dict[str, dict] = {}         # broker id -> {status, cum, avg, intent}
        self.positions: dict[str, int] = {}
        self.fail: Exception | None = None
        self.canceled: list[str] = []

    async def submit(self, intent):
        if self.fail:
            raise self.fail
        for bid, o in self.orders.items():        # adapter idempotency: same cid -> same order
            if o["intent"].client_order_id == intent.client_order_id:
                return OrderAck(intent.client_order_id, bid, OrderStatus.WORKING, T0)
        self.sent.append(intent)
        bid = str(len(self.orders) + 1)
        self.orders[bid] = {"status": OrderStatus.WORKING, "cum": 0, "avg": None, "intent": intent}
        return OrderAck(intent.client_order_id, bid, OrderStatus.WORKING, T0)

    async def cancel(self, cid):
        self.canceled.append(cid)
        for o in self.orders.values():
            if o["intent"].client_order_id == cid and o["status"] is OrderStatus.WORKING:
                o["status"] = OrderStatus.CANCELED
        return OrderAck(cid, "", OrderStatus.CANCELED, T0)

    async def order_snapshot(self, bid, cid):
        o = self.orders[bid]
        return OrderAck(cid, bid, o["status"], T0), o["cum"], o["avg"]

    async def signed_positions(self):
        return dict(self.positions)

    def fill(self, cid, cum, avg, done=True):
        for o in self.orders.values():
            if o["intent"].client_order_id == cid:
                sign = 1 if o["intent"].side is Side.BUY else -1
                self.positions[o["intent"].symbol] = self.positions.get(o["intent"].symbol, 0) + sign * (
                    cum - o["cum"])
                o.update(cum=cum, avg=avg, status=OrderStatus.FILLED if done else OrderStatus.WORKING)


@pytest.fixture
def env(tmp_path: Path):
    db = OpsStore(tmp_path / "ops.db")
    b = FakeBroker()
    clock = {"now": T0}
    p = Pilot(db, b, clock=lambda: clock["now"], adapter_lookup=lambda cid: None)
    return db, b, p, clock


async def test_entry_fill_exit_round_trip(env):
    db, b, p, _ = env
    cid = await p.place(strategy="6040", decision="2026-11", leg="SPY", symbol="SPY", side="BUY", qty=3,
                        marks=MARKS, limit_price=500.5)
    assert cid == "pilot:6040:2026-11:SPY" and b.sent[0].order_type is OrderType.LIMIT and not b.sent[0].short
    b.fill(cid, 3, 500.2)
    await p.sync()
    (pos,) = ledger.positions(db)
    assert (pos.qty, pos.cost) == (3, pytest.approx(500.2))
    out = await p.place(strategy="6040", decision="2026-12", leg="SPY", symbol="SPY", side="SELL", qty=3,
                        marks=MARKS, limit_price=499.0)
    b.fill(out, 3, 499.1)
    await p.sync()
    assert ledger.positions(db) == [] and await p.reconcile() == []


async def test_place_is_idempotent_across_restart(env):
    db, b, p, _ = env
    args = dict(strategy="tom", decision="2026-11-27", leg="entry", symbol="SPY", side="BUY", qty=2, marks=MARKS,
                limit_price=501.0)
    b.fail = BrokerUnavailableError("timeout")
    with pytest.raises(BrokerUnavailableError):
        await p.place(**args)
    assert ledger.get(db, "pilot:tom:2026-11-27:entry")["status"] == "reserved"   # budget held while ambiguous
    b.fail = None
    await p.place(**{**args, "limit_price": 502.0})       # retry re-sends the ORIGINAL order from the ledger
    await p.place(**args)
    assert len(b.sent) == 1 and b.sent[0].limit_price == 501.0


async def test_rejected_entry_releases_budget(env):
    db, b, p, _ = env
    b.fail = BrokerRejectedError("not shortable")
    with pytest.raises(BrokerRejectedError):
        await p.place(strategy="fade", decision="9", leg="entry", symbol="ABCD", side="SELL_SHORT", qty=50,
                      marks=MARKS, limit_price=9.98)
    assert ledger.get(db, "pilot:fade:9:entry")["status"] == "rejected"
    assert ledger.exposure(db, MARKS) == 0


@pytest.mark.parametrize("kw, msg", [
    (dict(strategy="6040", side="BUY", symbol="TSLA"), "allowlist"),
    (dict(strategy="fade", side="BUY", symbol="ABCD"), "may not"),
    (dict(strategy="6040", side="SELL_SHORT", symbol="SPY"), "may not"),
    (dict(strategy="6040", side="BUY", symbol="SPY", limit_price=520.0), "too far"),
    (dict(strategy="6040", side="BUY", symbol="SPY", limit_price=None), "limit"),
])
async def test_entry_guards(env, kw, msg):
    _, b, p, _ = env
    args = dict(decision="d", leg="e", qty=1, marks=MARKS, limit_price=500.0) | kw
    with pytest.raises(CapRefused, match=msg):
        await p.place(**args)
    assert b.sent == []


async def test_short_protective_stop_and_market_cover_without_price(env):
    db, b, p, _ = env
    e = await p.place(strategy="fade", decision="812", leg="entry", symbol="ABCD", side="SELL_SHORT", qty=100,
                      marks=MARKS, limit_price=9.99)
    assert b.sent[0].short and b.sent[0].side is Side.SELL
    b.fill(e, 100, 10.0)
    await p.sync()
    stop = await p.place(strategy="fade", decision="812", leg="stop", symbol="ABCD", side="BUY_TO_COVER", qty=100,
                         marks=MARKS, stop_price=12.0)
    assert b.sent[1].order_type is OrderType.STOP and b.sent[1].stop_price == 12.0 and b.sent[1].short
    with pytest.raises(CapRefused, match="working"):          # the stop already holds the full cover quantity
        await p.place(strategy="fade", decision="812", leg="cover1", symbol="ABCD", side="BUY_TO_COVER", qty=100,
                      marks=MARKS, limit_price=10.05)
    await p.cancel(stop)
    c = await p.place(strategy="fade", decision="812", leg="covermkt", symbol="ABCD", side="BUY_TO_COVER", qty=100,
                      marks={})                               # no quote at all: the exit still goes
    assert b.sent[2].order_type is OrderType.MARKET
    b.fill(c, 100, 10.3)
    await p.sync()
    assert ledger.positions(db) == []


async def test_unsent_reservation_expires_but_unknown_holds(env):
    db, b, p, clock = env
    ledger.reserve(db, client_order_id="pilot:tom:x:entry", strategy="tom", symbol="SPY", side="BUY", qty=1,
                   limit_price=500.0, marks=MARKS, now=T0)
    await p.sync()
    assert ledger.get(db, "pilot:tom:x:entry")["status"] == "reserved"   # maybe another process is sending it
    clock["now"] = T0 + timedelta(minutes=3)
    await p.sync()
    assert ledger.get(db, "pilot:tom:x:entry")["status"] == "expired"
    ledger.reserve(db, client_order_id="pilot:tom:y:entry", strategy="tom", symbol="SPY", side="BUY", qty=1,
                   limit_price=500.0, marks=MARKS, now=T0)
    p._lookup = lambda cid: ("unknown", None, {})
    assert "unknown" in (await p.sync())[0]
    assert ledger.get(db, "pilot:tom:y:entry")["status"] == "reserved"


async def test_reconcile_kills_entries_on_mismatch(env):
    db, b, p, _ = env
    e = await p.place(strategy="fade", decision="5", leg="entry", symbol="ABCD", side="SELL_SHORT", qty=100,
                      marks=MARKS, limit_price=10.0)
    b.fill(e, 100, 10.0)
    await p.sync()
    assert await p.reconcile() == []
    b.positions["ABCD"] = 0                                  # covered outside the pilot (e.g. in the app)
    assert await p.reconcile() == ["ABCD: pilot -100, Schwab +0"]
    with pytest.raises(CapRefused, match="paused"):
        await p.place(strategy="6040", decision="d", leg="SPY", symbol="SPY", side="BUY", qty=1, marks=MARKS,
                      limit_price=500.0)


async def test_flatten_closes_only_pilot_positions(env):
    db, b, p, _ = env
    b.positions["AAPL"] = 40                                 # the owner's own holding
    e = await p.place(strategy="fade", decision="7", leg="entry", symbol="ABCD", side="SELL_SHORT", qty=100,
                      marks=MARKS, limit_price=10.0)
    b.fill(e, 100, 10.0)
    s = await p.place(strategy="6040", decision="m", leg="SPY", symbol="SPY", side="BUY", qty=2, marks=MARKS,
                      limit_price=500.0)
    b.fill(s, 2, 500.0)
    await p.sync()
    stop = await p.place(strategy="fade", decision="7", leg="stop", symbol="ABCD", side="BUY_TO_COVER", qty=100,
                         marks=MARKS, stop_price=12.0)
    lines = await p.flatten({"ABCD": 10.0, "SPY": 500.0, "AAPL": 200.0})
    assert stop in b.canceled
    closes = [i for i in b.sent if i.client_order_id.startswith("pilot:") and "flat" in i.client_order_id]
    assert sorted((i.symbol, i.qty, i.limit_price) for i in closes) == [("ABCD", 100, 10.05), ("SPY", 2, 497.5)]
    assert all("AAPL" not in line for line in lines) and db.flag(ledger.KILL_FLAG) == "1"


def test_tick_rounds_toward_marketable():
    assert tick(10.051, up=True) == 10.06 and tick(10.059, up=False) == 10.05
    assert tick(0.12345, up=True) == 0.1235 and tick(10.05, up=True) == 10.05
    with pytest.raises(ValueError):
        tick(float("nan"), up=True)
