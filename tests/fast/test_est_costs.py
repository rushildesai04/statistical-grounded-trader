"""Live estimates after estimated costs (2026-11-16): the cost a text or a card subtracts is the official model's."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sgt.brokers.costs import GAPPER_COST_MODEL, GAPPER_OPEN_HOUR_MULT, GAPPER_REST_MULT
from sgt.fast import est_costs, long_shadow

OPEN_HOUR = "2026-11-16T14:45:00+00:00"            # 09:45 ET: first hour of the session
MIDDAY = "2026-11-16T17:45:00+00:00"               # 12:45 ET


def test_cost_is_the_official_model_at_the_live_entry():
    hs = GAPPER_COST_MODEL.half_spread_pct(3.0)
    assert est_costs.cost("long", "X", OPEN_HOUR, 3.0) == pytest.approx(2 * hs * GAPPER_OPEN_HOUR_MULT)
    assert est_costs.cost("long", "X", MIDDAY, 3.0) == pytest.approx(2 * hs * GAPPER_REST_MULT)
    assert est_costs.cost("long", "X", MIDDAY, 3.0) == pytest.approx(0.00734, abs=5e-6)       # pinned: 0.73% at $3
    assert est_costs.cost("short", "X", OPEN_HOUR, 3.0) == pytest.approx(0.0175, abs=5e-6)    # spread 1.25% + locate
    # a short pays the locate the official fade result charges; one constant across the codebase
    assert est_costs.cost("short", "X", MIDDAY, 3.0) - est_costs.cost("long", "X", MIDDAY, 3.0) == pytest.approx(0.005)
    assert est_costs.LOCATE == long_shadow.LOCATE
    # a datetime works like its text; a naive one is read as UTC
    at = datetime(2026, 11, 16, 17, 45, tzinfo=timezone.utc)
    assert est_costs.cost("long", "X", at, 3.0) == est_costs.cost("long", "X", at.replace(tzinfo=None), 3.0) \
        == est_costs.cost("long", "X", MIDDAY, 3.0)


def test_cheaper_stocks_cost_more_and_no_time_never_gets_the_cheap_rate():
    assert est_costs.cost("long", "X", MIDDAY, 0.8) > est_costs.cost("long", "X", MIDDAY, 3.0) \
        > est_costs.cost("long", "X", MIDDAY, 12.0) > 0
    for bad in (None, "", "not a time", 5):
        assert est_costs.cost("long", "X", bad, 3.0) == pytest.approx(est_costs.cost("long", "X", OPEN_HOUR, 3.0))


def test_no_price_means_no_estimate_never_a_free_trade():
    for px in (None, 0, -1, "x", float("nan")):
        assert est_costs.cost("short", "X", MIDDAY, px) is None
        assert est_costs.net("short", "X", MIDDAY, px, 2.0) is None
    for out in (None, 0, "x"):
        assert est_costs.net("long", "X", MIDDAY, 3.0, out) is None


def test_net_is_the_move_less_the_cost_on_both_sides():
    c_long, c_short = est_costs.cost("long", "X", MIDDAY, 4.0), est_costs.cost("short", "X", MIDDAY, 4.0)
    assert est_costs.net("long", "X", MIDDAY, 4.0, 4.2) == pytest.approx(0.05 - c_long)
    assert est_costs.net("short", "X", MIDDAY, 4.0, 3.8) == pytest.approx(0.05 - c_short)
    assert est_costs.net("long", "X", MIDDAY, 4.0, 4.0) == pytest.approx(-c_long)             # flat trade: a loss
    # a 4-cent target on a $3.82 stock (as VTSI 11-13) is inside the first-hour cost: a "win" at the target loses
    assert est_costs.net("long", "X", OPEN_HOUR, 3.82, 3.86) < 0
