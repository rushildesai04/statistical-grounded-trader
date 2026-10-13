"""Broker fault handling: bounded resubmit on transient failure (no duplicate order,
same client_order_id) and fail-closed disarm on auth failure."""
from __future__ import annotations

from live_fakes import FaultSimBroker, RecordingNotifier, build_executor, dataset

from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.strategy.runtime import EntryOverride


async def test_unavailable_submit_retries_same_order_no_duplicate():
    adapter = FaultSimBroker("acct", DEFAULT_COST_MODEL, 100_000.0, unavailable_times=1)
    h = build_executor(adapter=adapter, arm=True, entry_override=EntryOverride("take_every"),
                       submit_retries=3)
    await h.drive(dataset(["AAAA"]))
    orders = h.order_events("buy")
    assert orders, "a transient failure must not lose the order"
    cids = [p["client_order_id"] for p in orders]
    assert len(cids) == len(set(cids)), "no duplicate orders"
    # Exactly one extra attempt (the injected transient failure), reusing the same id.
    assert adapter.submit_attempts == adapter.distinct_orders + 1


async def test_auth_error_disarms_and_stops_new_submits():
    rec = RecordingNotifier()
    adapter = FaultSimBroker("acct", DEFAULT_COST_MODEL, 100_000.0, auth_fail=True)
    h = build_executor(adapter=adapter, arm=True, entry_override=EntryOverride("take_every"),
                       notifier=rec)
    await h.drive(dataset(["AAAA"]))
    assert not h.store.get_switch().armed, "an auth failure must disarm the namespace"
    assert adapter.submit_attempts == 1, "no further submits after fail-closed disarm"
    assert h.order_events("buy") == []
    assert any("AUTH" in title for title, _ in rec.sent)
