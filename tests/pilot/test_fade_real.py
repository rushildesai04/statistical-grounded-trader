from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from sgt.core.types import OrderType
from sgt.ops.store import OpsStore, iso
from sgt.pilot import fade_real, ledger
from sgt.pilot.executor import Pilot

from tests.pilot.test_executor import FakeBroker

T0 = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)            # Monday 10:00 ET
OK_FLAGS = {"closing_only": False, "equity": 30_000.0, "day_trader": False, "round_trips": 0}


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.db = OpsStore(tmp_path / "ops.db")
        self.b = FakeBroker()
        self.now = T0
        self.pilot = Pilot(self.db, self.b, clock=lambda: self.now, adapter_lookup=lambda cid: None)
        self.raw: list[str] = []
        self.px = {"ABCD": 10.0}
        self.flags = dict(OK_FLAGS)

    def trade(self, symbol="ABCD", entered=T0, status="open") -> int:
        with self.db.tx() as c:
            return c.execute(
                "INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, reason, "
                "est_entry, est_entry_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (symbol, "2026-11-09", "volume_spike", iso(entered), "c", 1, status, "veto", 10.0, iso(entered),
                 iso(entered))).lastrowid

    @property
    def texts(self) -> list[str]:                                   # styled (math bold) -> plain for matching
        return [unicodedata.normalize("NFKC", t) for t in self.raw]

    def live(self) -> None:
        self.db.set_flag(fade_real.LIVE_FLAG, "1")
        self.db.set_flag("fast_armed", "2026-11-09")

    async def step(self, dt: float = 0) -> None:
        self.now += timedelta(seconds=dt)

        async def flags():
            return dict(self.flags)
        await fade_real.step(self.pilot, self.db, lambda s: self.px.get(s), self.raw.append, flags, now=self.now)

    def sent(self, leg: str):
        return next(i for i in self.b.sent if i.client_order_id.endswith(":" + leg))


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


async def test_paper_only_unless_live_and_armed(env):
    rid = env.trade()
    await env.step()
    env.db.set_flag(fade_real.LIVE_FLAG, "1")                   # live on but not ARMed today
    await env.step(5)
    assert env.b.sent == [] and ledger.get(env.db, f"pilot:fade:{rid}:entry") is None


async def test_full_lifecycle_entry_stop_cover_report(env):
    env.live()
    rid = env.trade()
    await env.step(10)
    e = env.sent("entry")
    assert (e.qty, e.limit_price, e.short) == (50, 9.98, True)            # $500 / $10, 0.2% through
    env.b.fill(e.client_order_id, 50, 9.99)
    await env.step(10)
    s = env.sent("stop")
    assert s.order_type is OrderType.STOP and s.stop_price == pytest.approx(11.99) and s.qty == 50
    await env.step(600)                                         # still open, nothing new
    assert len(env.b.sent) == 2
    with env.db.tx() as c:
        c.execute("UPDATE fade_trades SET status='est_closed', reason='your NO' WHERE id=?", (rid,))
    await env.step(10)
    assert s.client_order_id in env.b.canceled
    await env.step(10)
    c1 = env.sent("cover1")
    assert (c1.qty, c1.limit_price) == (50, 10.02)
    env.b.fill(c1.client_order_id, 50, 9.5)
    await env.step(10)
    assert ledger.positions(env.db) == []
    assert any("REAL OUT" in t and "+24.50" in t for t in env.texts)
    await env.step(10)
    assert sum("REAL OUT" in t for t in env.texts) == 1


async def test_time_exit_escalates_and_never_cancels_market(env):
    env.live()
    rid = env.trade()
    await env.step(10)
    env.b.fill(env.sent("entry").client_order_id, 50, 10.0)
    await env.step(10)                                          # stop placed
    await env.step(3600)                                        # 60 min since the real entry
    await env.step(10)
    c1 = env.sent("cover1")
    await env.step(25)                                          # unfilled -> canceled
    assert c1.client_order_id in env.b.canceled
    await env.step(10)
    c2 = env.sent("cover2")
    assert c2.limit_price == pytest.approx(10.1)
    await env.step(25)
    await env.step(10)
    m = env.sent("covermkt")
    assert m.order_type is OrderType.MARKET and m.qty == 50
    await env.step(200)
    assert m.client_order_id not in env.b.canceled and any("not filled yet" in t for t in env.texts)
    assert ledger.get(env.db, f"pilot:fade:{rid}:covermkt")["status"] == "working"


async def test_near_close_goes_straight_to_market(env):
    env.live()
    env.now = datetime(2026, 11, 9, 20, 55, tzinfo=timezone.utc)   # 15:55 ET
    env.trade(entered=env.now)
    await env.step(5)
    assert env.b.sent == []                                     # too close to the close to open
    env.now = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
    env.trade(symbol="WXYZ", entered=env.now)
    env.px["WXYZ"] = 5.0
    await env.step(5)
    env.b.fill(env.sent("entry").client_order_id, 100, 5.0)
    env.now = datetime(2026, 11, 9, 20, 58, tzinfo=timezone.utc)  # 15:58 ET, inside the guard
    await env.step()                                            # stop canceled
    await env.step(5)
    assert env.sent("covermkt").order_type is OrderType.MARKET


async def test_broker_stop_fill_needs_no_cover(env):
    env.live()
    env.trade()
    await env.step(10)
    env.b.fill(env.sent("entry").client_order_id, 50, 10.0)
    await env.step(10)
    env.b.fill(env.sent("stop").client_order_id, 50, 12.0)       # squeeze: Schwab's stop covered it
    await env.step(10)
    assert ledger.positions(env.db) == [] and not any(i.client_order_id.endswith("cover1") for i in env.b.sent)
    assert any("REAL OUT" in t and "-100.00" in t for t in env.texts)
    assert fade_real.realized(env.db, "2026-11-09") == pytest.approx(-100.0)


async def test_late_signal_pdt_and_day_loss_block_entries(env):
    env.live()
    env.trade(entered=T0 - timedelta(minutes=3))
    await env.step()
    assert env.b.sent == []                                     # >2 min after the paper entry
    env.flags = {**OK_FLAGS, "equity": 9_000.0, "round_trips": 3}
    env.trade(symbol="WXYZ")
    env.px["WXYZ"] = 5.0
    await env.step()
    await env.step(10)
    assert env.b.sent == [] and sum("3 day trades" in t for t in env.texts) == 1
    env.flags = {**OK_FLAGS, "closing_only": None}
    assert fade_real.pdt_block(env.flags) == "Schwab day-trade status unreadable"
    assert fade_real.pdt_block({**OK_FLAGS, "equity": 9_000.0, "round_trips": "x"}).endswith("unreadable")
    env.db.set_flag(fade_real.DAY_LOSS_FLAG, "50")
    env.flags = dict(OK_FLAGS)
    with env.db.tx() as c:                                      # an earlier real loss of $100 today
        c.execute("INSERT INTO pilot_orders VALUES ('pilot:fade:1:entry','fade','OLD','SELL_SHORT',10,10,'limit',1,"
                  "'filled',10,10.0,NULL,NULL,?,?)", (iso(T0), iso(T0)))
        c.execute("INSERT INTO pilot_orders VALUES ('pilot:fade:1:stop','fade','OLD','BUY_TO_COVER',10,20,'stop',0,"
                  "'filled',10,20.0,NULL,NULL,?,?)", (iso(T0), iso(T0)))
    env.trade(symbol="EFGH")
    env.px["EFGH"] = 5.0
    await env.step()
    assert env.b.sent == [] and any("stopped for today" in t for t in env.texts)


async def test_refused_by_caps_texts_once(env):
    env.live()
    ledger.kill(env.db)
    env.trade()
    await env.step()
    await env.step(10)
    assert env.b.sent == [] and sum("not placed" in t for t in env.texts) == 1


async def test_rejected_short_reports_not_borrowable(env):
    from sgt.core.interfaces import BrokerRejectedError
    env.live()
    env.trade()
    env.b.fail = BrokerRejectedError("no shares to borrow")
    await env.step()
    env.b.fail = None
    await env.step(10)
    assert sum("not borrowable" in t for t in env.texts) == 1 and len(env.b.sent) == 0


async def test_real_fade_still_trades_while_6040_is_held(env):
    """Review 2026-11-05 L2: the total cap values every pilot position; the entry must quote them all."""
    env.live()
    ledger.init(env.db)
    with env.db.tx() as c:
        c.execute("INSERT INTO pilot_positions VALUES ('6040','SPY',3,600,?), ('6040','IEF',16,95,?)",
                  (iso(T0), iso(T0)))
    env.px.update({"SPY": 600.0, "IEF": 95.0})
    env.trade()
    await env.step(10)
    assert env.sent("entry").qty == 50
    env.px.pop("SPY")                                            # quote outage on a holding: valued at cost x 1.2
    env.trade(symbol="WXYZ")
    env.px["WXYZ"] = 5.0
    await env.step(10)
    assert any(i.symbol == "WXYZ" for i in env.b.sent)
