"""Restart position reconstruction (G-1): seed engine + lifecycle from broker state,
adopt an existing broker stop or place one, exit on signal, never re-enter while held."""
from __future__ import annotations

from datetime import datetime, timezone

from live_fakes import CFG, rth_bars, sim_with_position

from sgt.accounts.registry import AccountSpec
from sgt.core.types import Mode
from sgt.engine.backtest import default_risk_config
from sgt.live.replay import InMemoryLiveSource
from sgt.live.runner import LiveRunner
from sgt.strategy.runtime import State

_SPEC = AccountSpec("acct", "sim", "cash", True, 1.0, 0.0)


async def _noop_sleep(_s: float) -> None:
    return None


def _seed_runner(adapter, *, qty=100):
    return LiveRunner(
        "tests/fixtures/trader_v0_mechanics.yaml", Mode.SHADOW, [_SPEC], source=InMemoryLiveSource([]),
        adapters={"acct": adapter}, risk_config=default_risk_config(CFG),
        clock=lambda: datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc), sleep=_noop_sleep,
        ledger_positions_provider=lambda aid: {"AAAA": qty}, run_id="seed-test")


def _stop_orders(sim):
    return [wk for wk in sim._orders.values() if wk.intent.order_type.value == "stop"]


async def test_restart_adopts_existing_broker_stop():
    sim = sim_with_position(qty=100, avg=6.0, with_stop=True, stop_price=5.8)
    r = _seed_runner(sim)
    await r.startup()
    assert "acct" not in r.executor._halted_accounts
    assert r.engine.runtime_for("AAAA").state is State.IN_POSITION
    # Adopted: the existing stop is tracked, no new order submitted.
    assert "existing-stop-1" in r.executor._lc("acct").open_order_ids()
    assert len(_stop_orders(sim)) == 1
    assert not any(e["kind"] == "order" for e in r.sink.events)


async def test_restart_places_stop_when_none_exists():
    sim = sim_with_position(qty=100, avg=6.0, with_stop=False)
    r = _seed_runner(sim)
    await r.startup()
    assert r.engine.runtime_for("AAAA").state is State.IN_POSITION
    stops = _stop_orders(sim)
    assert len(stops) == 1, "a protective stop must be placed when none exists"
    orders = [e["payload"] for e in r.sink.events if e["kind"] == "order"]
    assert orders and orders[0]["order_type"] == "stop"
    assert r.executor._lc("acct").open_order_ids(), "lifecycle tracks the placed stop"


async def test_restart_exits_on_stop_and_never_reenters():
    sim = sim_with_position(qty=100, avg=6.0, with_stop=False)
    r = _seed_runner(sim)
    await r.startup()
    # Drive bars; the second gaps below the ~3% stop (avg 6.0 -> stop ~5.82).
    bars = rth_bars("AAAA", [6.0, 5.5, 5.4], lows=[5.95, 5.3, 5.35])
    for b in bars:
        await r._on_bar(b)
    assert r.executor.open_position_count() == 0, "the protective stop must close the position"
    sells = [e for e in r.sink.events if e["kind"] == "fill" and e["payload"]["side"] == "sell"]
    assert sells, "an exit fill is expected on the gap-down"
    assert not any(e["kind"] == "order" and e["payload"]["side"] == "buy" for e in r.sink.events), \
        "must never re-enter while the seeded position is held"
