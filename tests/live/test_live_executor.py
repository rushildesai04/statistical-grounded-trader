"""LiveExecutor money-path behaviors: disarmed placement block, armed placement,
duplicate-fill dedupe, and per-account halt."""
from __future__ import annotations

from live_fakes import DupFillBroker, build_executor, dataset

from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.strategy.runtime import EntryOverride


async def test_disarmed_places_no_orders():
    h = build_executor(arm=False, entry_override=EntryOverride("take_every"))
    await h.drive(dataset(["AAAA"]))
    assert h.order_events("buy") == []
    reasons = {r for p in h.events("risk_reject") for r in p["reasons"]}
    assert "disarmed" in reasons


async def test_armed_places_entry_orders():
    h = build_executor(arm=True, entry_override=EntryOverride("take_every"))
    await h.drive(dataset(["AAAA"]))
    assert len(h.order_events("buy")) >= 1


async def test_duplicate_fill_processed_once():
    adapter = DupFillBroker("acct", DEFAULT_COST_MODEL, 100_000.0)
    h = build_executor(adapter=adapter, arm=True, entry_override=EntryOverride("take_every"))
    await h.drive(dataset(["AAAA"]))
    fill_ids = [p["fill_id"] for p in h.events("fill")]
    assert len(fill_ids) == len(set(fill_ids)), "a fill_id must be processed exactly once"
    assert fill_ids, "the duplicate-fill broker still produced fills"


async def test_halted_account_places_no_orders():
    h = build_executor(arm=True, entry_override=EntryOverride("take_every"))
    h.executor.halt_account("acct", "reconcile_mismatch")
    await h.drive(dataset(["AAAA"]))
    assert h.order_events() == []
    assert any(e["kind"] == "account_halted" for e in h.sink.events)
