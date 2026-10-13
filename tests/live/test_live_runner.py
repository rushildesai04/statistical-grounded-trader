"""LiveRunner orchestration: feed-based mode refusal, startup reconciliation halt,
stale-feed entry block + recovery, heartbeat writes, and graceful shutdown."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest
from live_fakes import CFG, dataset

from sgt.accounts.registry import AccountSpec
from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.brokers.sim import SimBroker
from sgt.core.types import Mode
from sgt.db.session import get_session
from sgt.engine.backtest import default_risk_config
from sgt.live.models import LiveHeartbeat, LiveSession
from sgt.live.replay import InMemoryLiveSource
from sgt.live.runner import FeedNotTradeableError, LiveRunner

_SPEC = AccountSpec("acct", "sim", "cash", True, 1.0, 0.0)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


async def _noop_sleep(_s: float) -> None:
    return None


def _runner(source, *, mode=Mode.SHADOW, clock=None, ledger_positions=None,
            adapter=None, flatten_on_exit=False):
    adapter = adapter or SimBroker("acct", DEFAULT_COST_MODEL, 100_000.0)
    return LiveRunner(
        "tests/fixtures/trader_v0_mechanics.yaml", mode, [_SPEC], source=source,
        adapters={"acct": adapter}, risk_config=default_risk_config(CFG),
        clock=clock or Clock(datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc)),
        sleep=_noop_sleep, flatten_on_exit=flatten_on_exit,
        ledger_positions_provider=ledger_positions, run_id="run-test")


def test_live_refuses_delayed_and_iex_feeds():
    delayed = InMemoryLiveSource([], delay_minutes=15, consolidated=True)
    iex = InMemoryLiveSource([], delay_minutes=0, consolidated=False)
    with pytest.raises(FeedNotTradeableError):
        _runner(delayed, mode=Mode.LIVE)._check_feed()
    with pytest.raises(FeedNotTradeableError):
        _runner(iex, mode=Mode.LIVE)._check_feed()


def test_paper_refuses_delayed_but_shadow_accepts():
    delayed = InMemoryLiveSource([], delay_minutes=15, consolidated=True)
    with pytest.raises(FeedNotTradeableError):
        _runner(delayed, mode=Mode.PAPER)._check_feed()
    _runner(delayed, mode=Mode.SHADOW)._check_feed()  # must not raise


def test_live_accepts_realtime_consolidated():
    rt = InMemoryLiveSource([], delay_minutes=0, consolidated=True)
    _runner(rt, mode=Mode.LIVE)._check_feed()  # must not raise


async def test_startup_is_disarmed():
    r = _runner(InMemoryLiveSource([]))
    await r.startup()
    assert not r.risk._store.get_switch().armed


async def test_reconcile_mismatch_halts_account_no_orders():
    # Ledger claims a position the broker does not hold -> mismatch -> account halted.
    r = _runner(InMemoryLiveSource([]), ledger_positions=lambda aid: {"XYZ": 5})
    await r.startup()
    assert "acct" in r.executor._halted_accounts
    assert any(e["kind"] == "account_halted" for e in r.sink.events)
    # A halted account receives no order allocation.
    from sgt.strategy.base import Decision
    d = Decision(kind="entry", symbol="AAAA", reason="entry",
                 signal_ts=datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc),
                 limit_price=6.0, stop_price=5.8, qty_hint=10)
    assert r.executor._allocate(d) == []


async def test_stale_feed_blocks_entries_and_recovers():
    bars = dataset(["AAAA"], n_sessions=1)["AAAA"]
    rth = [b for b in bars if b.minute_of_day >= 600]
    b0, b1 = rth[10], rth[11]
    clock = Clock(b0.ts)
    r = _runner(InMemoryLiveSource(bars), clock=clock)
    await r.startup()
    clock.now = b0.ts
    await r._on_bar(b0)
    assert not r.executor._entries_blocked
    # No bar for > staleness during a tradable (RTH) window -> block.
    await r._check_staleness(b0.ts + timedelta(seconds=200))
    assert r.executor._entries_blocked
    # A fresh bar clears the block automatically.
    clock.now = b1.ts
    await r._on_bar(b1)
    assert not r.executor._entries_blocked


async def test_heartbeat_rows_written():
    r = _runner(InMemoryLiveSource([]))
    await r.startup()
    now = datetime(2024, 3, 12, 15, 1, tzinfo=timezone.utc)
    await r._write_heartbeat(now)
    await r._write_heartbeat(now + timedelta(minutes=1))
    with get_session() as s:
        rows = s.query(LiveHeartbeat).filter(LiveHeartbeat.run_id == "run-test").all()
    assert len(rows) == 2


async def test_graceful_shutdown_blocks_entries_flushes_and_keeps_stops():
    r = _runner(InMemoryLiveSource([]))
    await r.startup()
    await r.shutdown(flatten=False)
    assert r.executor._entries_blocked
    # Shutdown must not cancel protective stops or flatten.
    assert not any(e["kind"] == "cancel" for e in r.sink.events)
    assert not any(e["kind"] == "fill" for e in r.sink.events)
    with get_session() as s:
        row = s.get(LiveSession, "run-test")
    assert row is not None and row.status == "stopped" and row.ended_at is not None
