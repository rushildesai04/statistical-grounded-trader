"""AccountRouter: weighting, rounding, aggregate cap, exits, deterministic ids."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sgt.accounts.registry import AccountSpec
from sgt.accounts.router import AccountRouter, _largest_remainder
from sgt.core.types import Side

from acct_helpers import seeded_sim

UTC = timezone.utc
TS = datetime(2026, 10, 12, 14, 30, tzinfo=UTC)


def _spec(aid, weight=1.0, max_notional=0.0, enabled=True):
    return AccountSpec(aid, "sim", "cash", enabled, weight, max_notional)


def _router(specs, adapters, cap=1e12):
    return AccountRouter(specs, adapters, max_symbol_notional_total=cap)


def test_largest_remainder_sums_and_distributes():
    assert _largest_remainder(10, [1, 1, 1]) in ([4, 3, 3], [3, 4, 3], [3, 3, 4])
    assert sum(_largest_remainder(10, [1, 1, 1])) == 10
    assert _largest_remainder(8, [1, 3]) == [2, 6]


async def test_split_by_weight():
    a, b = _spec("A", 1.0), _spec("B", 3.0)
    adapters = {"A": seeded_sim("A", "AAA", 0), "B": seeded_sim("B", "AAA", 0)}
    router = _router([a, b], adapters)
    intents = await router.allocate("strat", "AAA", Side.BUY, 8, 5.0, TS, "entry", False)
    by_acct = {i.account_id: i.qty for i in intents}
    assert by_acct == {"A": 2, "B": 6}


async def test_per_account_max_notional_cap():
    a = _spec("A", 1.0, max_notional=25.0)  # 25/5 = 5 shares max
    b = _spec("B", 1.0, max_notional=0.0)   # uncapped
    adapters = {"A": seeded_sim("A", "AAA", 0), "B": seeded_sim("B", "AAA", 0)}
    router = _router([a, b], adapters)
    intents = await router.allocate("strat", "AAA", Side.BUY, 20, 5.0, TS, "entry", False)
    by_acct = {i.account_id: i.qty for i in intents}
    assert by_acct["A"] == 5  # capped
    assert by_acct["B"] == 10  # its half of the even split, uncapped


async def test_zero_legs_dropped():
    a, b = _spec("A", 1.0), _spec("B", 1_000_000.0)
    adapters = {"A": seeded_sim("A", "AAA", 0), "B": seeded_sim("B", "AAA", 0)}
    router = _router([a, b], adapters)
    intents = await router.allocate("strat", "AAA", Side.BUY, 1, 5.0, TS, "entry", False)
    assert [i.account_id for i in intents] == ["B"]  # A rounds to 0, dropped


async def test_aggregate_cap_across_accounts_with_existing_positions():
    # cap = $1000 at $5 = 200 shares total across ALL accounts.
    a, b = _spec("A", 1.0), _spec("B", 1.0)
    adapters = {"A": seeded_sim("A", "AAA", 150, 5.0),  # already holds 150
                "B": seeded_sim("B", "AAA", 0)}
    router = _router([a, b], adapters, cap=1000.0)
    intents = await router.allocate("strat", "AAA", Side.BUY, 100, 5.0, TS, "entry", False)
    # Only 50 more shares fit (200 - 150). Adding account B must not multiply exposure.
    assert sum(i.qty for i in intents) == 50


async def test_pending_open_orders_count_toward_cap():
    a = _spec("A", 1.0)
    adapters = {"A": seeded_sim("A", "AAA", 100, 5.0)}
    router = _router([a], adapters, cap=1000.0)  # 200 shares max
    intents = await router.allocate("strat", "AAA", Side.BUY, 100, 5.0, TS, "entry", False,
                                    pending_shares={"A": 60})
    # 100 held + 60 working = 160 -> only 40 more fit.
    assert sum(i.qty for i in intents) == 40


async def test_pending_open_orders_derived_from_adapter():
    # No pending_shares kwarg: the router must derive working BUY shares from
    # the adapter's open_orders() and count them toward the aggregate cap.
    from datetime import datetime, timezone
    from sgt.core.types import OrderIntent, OrderType, TimeInForce
    a = _spec("A", 1.0)
    sim = seeded_sim("A", "AAA", 100, 5.0)  # holds 100
    sim.submit_sync(OrderIntent(  # a working (unfilled) BUY of 60
        "work-1", "A", "AAA", Side.BUY, 60, OrderType.LIMIT, 1.0, TimeInForce.DAY,
        False, "strat", "entry", datetime(2026, 10, 12, 14, 29, tzinfo=timezone.utc)))
    router = _router([a], {"A": sim}, cap=1000.0)  # 200 shares max
    intents = await router.allocate("strat", "AAA", Side.BUY, 100, 5.0, TS, "entry", False)
    assert sum(i.qty for i in intents) == 40  # 100 held + 60 working -> only 40 fit


async def test_exit_never_oversells():
    a, b = _spec("A", 1.0), _spec("B", 1.0)
    adapters = {"A": seeded_sim("A", "AAA", 10, 5.0), "B": seeded_sim("B", "AAA", 4, 5.0)}
    router = _router([a, b], adapters)
    intents = await router.allocate("strat", "AAA", Side.SELL, 50, 5.0, TS, "exit:stop", False)
    by_acct = {i.account_id: i.qty for i in intents}
    assert by_acct == {"A": 10, "B": 4}  # capped at each account's held position
    assert all(i.side is Side.SELL for i in intents)


async def test_deterministic_client_order_ids():
    a, b = _spec("A", 1.0), _spec("B", 3.0)
    adapters = {"A": seeded_sim("A", "AAA", 0), "B": seeded_sim("B", "AAA", 0)}
    router = _router([a, b], adapters)
    i1 = await router.allocate("strat", "AAA", Side.BUY, 8, 5.0, TS, "entry", False)
    i2 = await router.allocate("strat", "AAA", Side.BUY, 8, 5.0, TS, "entry", False)
    assert [i.client_order_id for i in i1] == [i.client_order_id for i in i2]
    assert len({i.client_order_id for i in i1}) == len(i1)  # unique per leg
