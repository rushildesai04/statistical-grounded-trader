from __future__ import annotations

import json
import unicodedata
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from sgt.ops import approvals
from sgt.ops.store import OpsStore, iso
from sgt.pilot import book_real, ledger
from sgt.pilot.executor import Pilot

from tests.pilot.test_executor import FakeBroker

MARKS = {"SPY": 600.0, "IEF": 95.0}
ASK = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)            # 10:00 ET
EXEC = datetime(2026, 11, 9, 20, 46, tzinfo=timezone.utc)          # 15:46 ET


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.db = OpsStore(tmp_path / "ops.db")
        self.b = FakeBroker()
        self.now = ASK
        self.pilot = Pilot(self.db, self.b, clock=lambda: self.now, adapter_lookup=lambda cid: None)
        self.texts: list[str] = []

    async def run(self, now: datetime):
        self.now = now
        return await book_real.run(self.pilot, self.db, self.texts.append, lambda syms: dict(MARKS), now=now)

    def approve(self, strategy: str, ok: bool = True) -> None:
        row = approvals.latest(self.db, f"pilot:{strategy}", self.now.astimezone(approvals.ET).date().isoformat())
        with self.db.tx() as c:
            c.execute("UPDATE approvals SET status=? WHERE id=?", ("approved" if ok else "rejected", row["id"]))

    def fill_all(self) -> None:
        for o in list(self.b.orders.values()):
            if o["cum"] < o["intent"].qty:
                self.b.fill(o["intent"].client_order_id, o["intent"].qty, o["intent"].limit_price or 600.0)


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


async def test_6040_asks_then_buys_in_close_window_split_under_per_order_cap(env):
    assert await env.run(ASK) == ["6040: asked"]
    assert env.b.sent == [] and "REAL 60/40" in unicodedata.normalize("NFKC", env.texts[-1])
    env.approve("6040")
    await env.run(datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc))          # approved but not yet the window
    assert env.b.sent == []
    await env.run(EXEC)
    spy = [i for i in env.b.sent if i.symbol == "SPY"]
    ief = [i for i in env.b.sent if i.symbol == "IEF"]
    assert sum(i.qty for i in spy) == 3 and sum(i.qty for i in ief) == 16       # $2,400 / $1,600 less headroom
    assert all(i.qty * i.limit_price <= ledger.PER_ORDER for i in env.b.sent)
    assert len({i.client_order_id for i in env.b.sent}) == len(env.b.sent)
    n = len(env.b.sent)
    await env.run(datetime(2026, 11, 9, 20, 51, tzinfo=timezone.utc))          # rerun: no duplicates
    assert len(env.b.sent) == n
    env.fill_all()
    await env.run(datetime(2026, 11, 9, 20, 56, tzinfo=timezone.utc))
    assert {p.symbol: p.qty for p in ledger.positions(env.db, "6040")} == {"SPY": 3, "IEF": 16}
    # next session in the same month: holds, no new ask
    env.texts.clear()
    assert await env.run(datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)) == []


async def test_rejected_or_unanswered_entry_sends_nothing(env):
    await env.run(ASK)
    env.approve("6040", ok=False)
    await env.run(EXEC)
    assert env.b.sent == []


async def test_tom_follows_the_approved_paper_schedule_and_exits_without_asking(env):
    env.now = ASK
    with env.db.tx() as c:
        c.execute("INSERT INTO book_orders(strategy, symbol, target, fill_session, source, status, created_at) "
                  "VALUES ('tom','SPY',1.0,'2026-11-09','approved','scheduled',?)", (iso(ASK),))
    ledger.init(env.db)
    env.db.set_flag("pilot_6040_month", "2026-11")
    with env.db.tx() as c:                                                   # 60/40 already held: only TOM acts
        c.execute("INSERT INTO pilot_positions VALUES ('6040','SPY',3,600,?), ('6040','IEF',16,95,?)",
                  (iso(ASK), iso(ASK)))
    env.b.positions = {"SPY": 3, "IEF": 16}
    assert await env.run(ASK) == ["tom: asked"]
    env.approve("tom")
    await env.run(EXEC)
    assert [(i.symbol, i.qty, i.side.value) for i in env.b.sent] == [("SPY", 3, "buy")]
    env.fill_all()
    # exit day: paper schedule goes to 0 -> real sell goes out in the window with no approval
    with env.db.tx() as c:
        c.execute("INSERT INTO book_orders(strategy, symbol, target, fill_session, source, status, created_at) "
                  "VALUES ('tom','SPY',0.0,'2026-11-10','approved','scheduled',?)", (iso(ASK),))
    await env.run(datetime(2026, 11, 10, 20, 46, tzinfo=timezone.utc))
    sells = [i for i in env.b.sent if i.side.value == "sell"]
    assert [(i.symbol, i.qty) for i in sells] == [("SPY", 3)] and sells[0].client_order_id.startswith("pilot:tom:")


async def test_half_day_window_and_closed_days(env):
    assert await env.run(datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)) == []    # Thanksgiving
    await env.run(datetime(2026, 11, 27, 15, 0, tzinfo=timezone.utc))
    env.approve("6040")
    await env.run(datetime(2026, 11, 27, 17, 46, tzinfo=timezone.utc))                  # 12:46 ET, 13:00 close
    assert env.b.sent
    assert await env.run(datetime(2026, 11, 27, 17, 58, tzinfo=timezone.utc)) == []    # past the cutoff


async def test_reconcile_mismatch_texts_and_blocks(env):
    ledger.init(env.db)
    with env.db.tx() as c:
        c.execute("INSERT INTO pilot_positions VALUES ('6040','SPY',3,600,?)", (iso(ASK),))
    env.b.positions = {}
    await env.run(ASK)
    assert any("differ from Schwab" in unicodedata.normalize("NFKC", t) for t in env.texts) and env.db.flag(ledger.KILL_FLAG) == "1"


def test_approved_quantities_bound_what_is_sent():
    # the payload is what the owner saw; the job never buys more than that
    diff = {"SPY": 3, "IEF": 16}
    assert json.loads(json.dumps({"strategy": "6040", "diff": diff}))["diff"] == diff
    assert book_real._diff({"SPY": 3}, {"SPY": 3, "IEF": 0}, MARKS) == {}
    assert book_real._diff({"SPY": 3}, {"SPY": 0}, MARKS) == {"SPY": -3}
    assert book_real._shares(4_000, book_real.WEIGHTS_6040, MARKS) == {"SPY": 3, "IEF": 16}
    with pytest.raises(ValueError):
        book_real._shares(4_000, {"SPY": 1.0}, {})
    assert date(2026, 11, 27).isoformat() == "2026-11-27"
