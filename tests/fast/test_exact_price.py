"""Exact decimal price thresholds, ``price-decimal-v1`` (prepared 2026-11-25; not in use by any registered
calculation). The registered arithmetic (``binary-v0``) is tested beside it so the difference is on record, not so
that one is preferred."""
from __future__ import annotations

from decimal import Decimal as D

import numpy as np
import pytest

from sgt.fast import exact_price as xp


def test_a_price_is_the_decimal_its_source_sent_and_nothing_else_is_a_price():
    assert xp.price(1.2) == D("1.2") and xp.price(6.18) == D("6.18") and xp.price(0.9598) == D("0.9598")
    assert xp.price(np.float64(1.0339)) == D("1.0339") and xp.price("2.3631") == D("2.3631") and xp.price(3) == D(3)
    assert xp.price(0.000001) == D("0.000001") and xp.price(D("1.50")) == D("1.5")
    for bad in (0, -1.0, float("nan"), float("inf"), None, True, "abc", 1.2000000000000002, 6.180000000000001,
                1.0 / 3, 0.0000001):
        with pytest.raises(xp.NotAPrice):                        # never rounded into a price
            xp.price(bad)
    for bad in (0.2, None, True, "x", float("nan")):
        with pytest.raises(TypeError):                           # a rate must be exact: 0.2 as a double is not 0.2
            xp.rate(bad)


def test_a_level_is_exact_and_is_never_rounded_to_a_tick():
    assert xp.level_above(1.0) == D("1.2") and xp.level_above(5.15) == D("6.18")
    assert xp.level_above(1.9692) == D("2.36304")                # no quote can sit there: the level stays what it is
    assert xp.level_above("0.8001") == D("0.96012") and xp.level_above(1.02) == D("1.224")
    assert xp.level_below(0.7) == D("0.63") and xp.level_below(1.44) == D("1.296")
    assert xp.long_target(0.9299, 0.8949) == D("0.9999") and xp.long_target(10, 9.5) == D("11")
    assert xp.first_quote_at_or_above(D("2.36304")) == D("2.37") and xp.first_quote_at_or_above(D("1.2")) == D("1.2")
    assert xp.first_quote_at_or_above(D("0.96012")) == D("0.9602") and xp.first_quote_at_or_above(D("0.99995")) == D("1")
    assert xp.quote_tick(1.0) == D("0.01") and xp.quote_tick(0.9999) == D("0.0001")


def test_an_exact_touch_counts_and_the_prices_right_next_to_it_fall_on_their_own_side():
    """Supported increments: a cent at $1 and above, $0.0001 below, and trades printing inside the cent."""
    for entry, level in ((1.0, "1.2"), (5.15, "6.18"), (9.05, "10.86"), (16.85, "20.22"), (1.02, "1.224")):
        lv = D(level)
        assert xp.level_above(entry) == lv
        assert xp.decimal_short_stop(entry, float(lv))                           # the touch itself
        assert xp.decimal_short_stop(entry, float(lv + D("0.0001"))) and xp.decimal_short_stop(entry, float(lv + D("0.01")))
        assert not xp.decimal_short_stop(entry, float(lv - D("0.0001")))         # one sub-penny print under it
        assert not xp.decimal_short_stop(entry, float(lv - D("0.01")))           # one cent under it
    # a level between ticks: nothing can touch it; the first print over it counts, the last under it does not
    assert not xp.decimal_short_stop(1.9692, 2.363) and xp.decimal_short_stop(1.9692, 2.3631)
    assert not xp.decimal_short_stop(1.9692, 2.36) and xp.decimal_short_stop(1.9692, 2.37)
    # under a dollar the grid is $0.0001
    assert xp.decimal_short_stop(0.8, 0.96) and not xp.decimal_short_stop(0.8, 0.9599) and xp.decimal_short_stop(0.8, 0.9601)
    # a long's target and the price test, the same way
    assert xp.decimal_long_target(10.0, 9.5, 11.0) and not xp.decimal_long_target(10.0, 9.5, 10.99)
    assert xp.decimal_price_test(0.7, 0.63) and not xp.decimal_price_test(0.7, 0.6301) and xp.decimal_price_test(0.7, 0.6299)


def test_where_the_registered_binary_comparison_and_the_decimal_one_disagree():
    """The defect, counted: on the grid of five-cent entries from $1 to $50 (the only entries whose 20% level is a
    whole cent, so the only ones a quote can touch exactly) the registered comparison misses the touch at 23 of 981."""
    grid = [c / 100 for c in range(100, 5001, 5)]
    touch = [float(xp.level_above(e)) for e in grid]
    missed = [e for e, t in zip(grid, touch) if xp.decimal_short_stop(e, t) and not xp.binary_short_stop(e, t)]
    extra = [e for e, t in zip(grid, touch) if xp.binary_short_stop(e, t) and not xp.decimal_short_stop(e, t)]
    assert len(grid) == 981 and len(missed) == 23 and extra == []
    assert missed[:4] == [5.15, 9.05, 10.3, 11.55]
    assert xp.binary_short_stop(1.0, 1.2) and xp.decimal_short_stop(1.0, 1.2)       # GLXG 2026-11-24: both count it
    # one tick away from the level the two always agree: only the exact touch is in question
    for e, t in zip(grid, touch):
        up, down = float(D(repr(t)) + D("0.0001")), float(D(repr(t)) - D("0.0001"))
        assert xp.binary_short_stop(e, up) and xp.decimal_short_stop(e, up)
        assert not xp.binary_short_stop(e, down) and not xp.decimal_short_stop(e, down)
    # the price test (10% under the prior close) has the same class of miss in the registered form
    closes = [c / 100 for c in range(100, 5001)]
    trig = [(c, float(xp.level_below(c))) for c in closes if -xp.level_below(c).normalize().as_tuple().exponent <= 2]
    miss = [c for c, t in trig if xp.decimal_price_test(c, t) and not xp.binary_price_test(c, t)]
    assert len(trig) == 491 and len(miss) > 0 and all(xp.decimal_price_test(c, t) for c, t in trig)


def test_the_replay_under_the_decimal_version_books_the_same_things_the_rule_names():
    highs, opens, closes = [1.04, 1.2, 0.95], [1.0, 1.125, 0.95], [1.02, 1.153, 0.94]
    assert xp.short_cover(highs, opens, closes, 1.0) == (D("1.2"), 1)               # a touch: covered at the level
    assert xp.short_cover([1.04, 1.1999, 0.95], opens, closes, 1.0) == (D("0.94"), None)   # the timer: the last close
    assert xp.short_cover([1.04, 1.4], [1.0, 1.31], [1.02, 1.3], 1.0) == (D("1.31"), 1)     # a gap: that bar's open
    assert xp.short_cover([6.0, 6.18], [5.15, 6.0], [5.9, 6.1], 5.15) == (D("6.18"), 1)     # where binary-v0 says no
    assert not xp.binary_short_stop(5.15, 6.18)
    assert xp.short_cover([], [], [], 1.0) is None
    assert xp.VERSION == "price-decimal-v1" and xp.REGISTERED == "binary-v0"


def test_no_step_of_the_arithmetic_is_allowed_to_round():
    assert xp.level_above("12345.678901", "0.123456789") == D("12345.678901") * D("1.123456789")
    with pytest.raises(Exception):                                # a result that would need rounding is an error
        xp._EXACT.divide(D(1), D(3))
