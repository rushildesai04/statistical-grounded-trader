"""Fixed-risk sizing and meta-filter size-by-score."""
from __future__ import annotations

from sgt.strategy.schema import Sizing
from sgt.strategy.sizing import fixed_risk_qty, size_for_decision


def _sizing(**kw) -> Sizing:
    base = dict(method="fixed_risk", risk_per_trade_pct_of_allocation=0.5,
                allocation_usd=10_000.0, max_notional_usd=20_000.0)
    base.update(kw)
    return Sizing(**base)


def test_fixed_risk_qty_basic():
    s = _sizing()
    # risk budget = 10000 * 0.5% = $50; per-share risk = 10 - 9.7 = 0.30 -> 166 shares.
    assert fixed_risk_qty(s, entry_price=10.0, stop_price=9.7) == 166


def test_fixed_risk_capped_by_max_notional():
    s = _sizing(allocation_usd=1_000_000.0, max_notional_usd=1_000.0)
    # notional cap = floor(1000 / 10) = 100 shares regardless of the large risk budget.
    assert fixed_risk_qty(s, entry_price=10.0, stop_price=9.7) == 100


def test_zero_or_invalid_distance_is_skip():
    s = _sizing()
    assert fixed_risk_qty(s, entry_price=10.0, stop_price=10.0) == 0
    assert fixed_risk_qty(s, entry_price=10.0, stop_price=11.0) == 0
    s2 = _sizing(allocation_usd=1.0, max_notional_usd=20_000.0)
    assert fixed_risk_qty(s2, entry_price=1000.0, stop_price=999.0) == 0  # < 1 share


def test_size_by_score_never_exceeds_base():
    s = _sizing()
    base = size_for_decision(s, 10.0, 9.7)
    scaled = size_for_decision(s, 10.0, 9.7, score=0.5, size_by_score=True)
    assert 0 < scaled <= base
    assert scaled == base // 2 or scaled == base // 2  # floor(base * 0.5)
    # disabled -> base regardless of score
    assert size_for_decision(s, 10.0, 9.7, score=0.1, size_by_score=False) == base
