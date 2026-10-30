"""Runner lifecycles against a fake paper broker and a controllable clock."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sgt.signals_live import runner as rmod
from sgt.signals_live.runner import Runner

T0 = datetime(2026, 10, 29, 14, 31, 55, tzinfo=timezone.utc)  # 10:31:55 ET
NVB, IDEAS = -1, -2


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeBroker:
    def __init__(self, price=4.0):
        self.price, self.orders, self.pos = price, {}, {}
        self.equity, self.last_equity, self.flattened = 100_000.0, 100_000.0, 0

    async def last_price(self, s):
        return self.price

    async def account(self):
        return {"equity": str(self.equity), "last_equity": str(self.last_equity)}

    async def positions(self):
        return self.pos

    async def order(self, cid):
        return self.orders.get(cid)

    async def market(self, symbol, qty, side, cid):
        self.orders[cid] = {"id": cid, "status": "new", "symbol": symbol, "qty": qty, "side": side,
                            "filled_qty": "0", "filled_avg_price": None}
        return self.orders[cid]

    async def bracket_buy(self, symbol, qty, limit, tp, stop, cid):
        self.orders[cid] = {"id": cid, "status": "new", "filled_qty": "0", "filled_avg_price": None,
                            "legs": [{"status": "held", "filled_qty": "0", "tp": tp},
                                     {"status": "held", "filled_qty": "0", "stop": stop}], "qty": qty}
        return self.orders[cid]

    def fill(self, cid, px, at):
        o = self.orders[cid]
        o.update(status="filled", filled_qty=str(o["qty"]), filled_avg_price=str(px),
                 filled_at=at.isoformat())

    async def cancel(self, oid):
        self.orders[oid]["status"] = "canceled"

    async def flatten_all(self):
        self.flattened += 1


class FakeNotify:
    def __init__(self):
        self.msgs = []

    async def send(self, text, important=True):
        self.msgs.append((text, important))


@pytest.fixture
def env(tmp_path, monkeypatch):
    clock = Clock(T0)
    monkeypatch.setattr(rmod, "_now", clock)
    monkeypatch.setattr(rmod, "JOURNAL", tmp_path)
    b, n = FakeBroker(), FakeNotify()
    return Runner(None, b, n, nvb=NVB, ideas=IDEAS, trade_alerts=True), b, n, clock


async def test_alert_lifecycle(env):
    r, b, n, clock = env
    await r.on_message(NVB, 7, T0, "ABCD  volume spike  4.00", False)
    [t] = r.trades
    assert t.status == "scheduled" and "ABCD" in r.state.open_symbols
    clock.t = datetime(2026, 10, 29, 14, 33, tzinfo=timezone.utc)
    await r.tick()
    assert b.orders["nvb-7-ABCD"]["side"] == "buy" and t.status == "entering"
    b.fill("nvb-7-ABCD", 4.00, clock.t)
    await r.tick()
    assert t.status == "open" and t.exit_at == clock.t + timedelta(minutes=15)
    clock.t += timedelta(minutes=15)
    await r.tick()
    assert b.orders["x-nvb-7-ABCD"]["side"] == "sell" and t.status == "exiting"
    b.fill("x-nvb-7-ABCD", 4.20, clock.t)
    await r.tick()
    assert t.status == "closed" and "ABCD" not in r.state.open_symbols
    assert any("SOLD 500 ABCD @ $4.20: +$100" in m for m, _ in n.msgs)
    # same type same day is not traded again
    await r.on_message(NVB, 8, clock.t, "ABCD  volume spike  4.30", False)
    assert any("already traded" in m for m, _ in n.msgs)


async def test_stale_and_edited_messages_not_traded(env):
    r, b, n, clock = env
    clock.t = T0 + timedelta(minutes=5)
    await r.on_message(NVB, 7, T0, "ABCD  volume spike  4.00", False)
    await r.on_message(NVB, 9, clock.t, "WXYZ  volume spike  4.00", True)
    assert r.trades == [] and any("stale" in m for m, _ in n.msgs)


async def test_idea_bracket_lifecycle(env):
    r, b, n, clock = env
    b.price = 8.30
    text = "🚨 STOCKS\n\n$PCLA\n\nIdeal Entry: 8.36\n\nPT: 5% , 10% , 20%+\n\nSL: 7.02"
    await r.on_message(IDEAS, 11, T0, text, False)
    [t] = r.trades
    cids = [c for c in b.orders if c.startswith("idea-11-PCLA")]
    assert len(cids) == 3 and sum(b.orders[c]["qty"] for c in cids) == 238
    for c in cids:
        b.fill(c, 8.36, clock.t)
    await r.tick()
    assert t.status == "open" and t.entry_px == pytest.approx(8.36)
    for c in cids:
        leg = b.orders[c]["legs"][0]
        leg.update(status="filled", filled_qty=str(b.orders[c]["qty"]), filled_avg_price="8.78")
    await r.tick()
    assert t.status == "closed"
    assert any("SOLD 238 PCLA @ $8.78" in m for m, _ in n.msgs)


async def test_idea_cancelled_when_unfilled(env):
    r, b, n, clock = env
    b.price = 8.30
    await r.on_message(IDEAS, 12, T0, "$PCLA\nIdeal Entry: 8.36\nPT: 5%\nSL: 7.02", False)
    clock.t += timedelta(minutes=11)
    await r.tick()
    assert r.trades[0].status == "cancelled" and "PCLA" not in r.state.open_symbols


async def test_kill_flattens_and_pauses(env):
    r, b, n, clock = env
    await r.on_message(NVB, 7, T0, "ABCD  volume spike  4.00", False)
    reply = await r.on_command("kill")
    assert "KILLED" in reply and b.flattened == 1 and r.state.paused and not r.state.open_symbols
    await r.on_message(NVB, 8, T0, "WXYZ  volume spike  4.00", False)
    assert any("paused" in m for m, _ in n.msgs)
    assert await r.on_command("resume") == "resumed" and not r.state.paused


async def test_daily_loss_stop(env):
    r, b, n, clock = env
    b.equity = 98_900.0
    await r.check_daily_loss()
    assert r.state.paused and any("DAILY LOSS STOP" in m for m, _ in n.msgs)


async def test_end_of_day_flatten_once(env):
    r, b, n, clock = env
    b.pos = {"ABCD": {"qty": "10", "qty_available": "0", "avg_entry_price": "4"}}
    clock.t = datetime(2026, 10, 29, 19, 56, tzinfo=timezone.utc)  # 15:56 ET
    await r.tick()
    await r.tick()
    assert b.flattened == 1


async def test_end_of_day_quiet_when_flat(env):
    r, b, n, clock = env
    clock.t = datetime(2026, 10, 30, 3, 0, tzinfo=timezone.utc)  # 23:00 ET, nothing held
    await r.tick()
    assert b.flattened == 0 and n.msgs == []


async def test_restart_exits_unprotected_position(env):
    r, b, n, clock = env
    b.pos = {"ABCD": {"qty": "100", "qty_available": "100", "avg_entry_price": "4.0"}}
    await r.reconcile()
    await r.tick()
    assert any(c.startswith("x-recover-ABCD") for c in b.orders)


async def test_alerts_watch_only_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(rmod, "_now", Clock(T0))
    monkeypatch.setattr(rmod, "JOURNAL", tmp_path)
    b, n = FakeBroker(), FakeNotify()
    r = Runner(None, b, n, nvb=NVB, ideas=IDEAS)
    await r.on_message(NVB, 7, T0, "ABCD  volume spike  4.00", False)
    assert r.trades == [] and not r.state.open_symbols and b.orders == {}
    assert any("watch ABCD" in m and not imp for m, imp in n.msgs)
