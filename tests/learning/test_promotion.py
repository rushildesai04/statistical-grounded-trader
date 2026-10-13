"""Promotion-gate tests (plan §9.2)."""
from __future__ import annotations

import numpy as np
import pytest

from sgt.learning.promotion import promotion_gate


def _m(returns) -> dict:
    return {"returns": returns}


def test_promotes_clear_out_of_sample_winner():
    rng = np.random.default_rng(0)
    cand = rng.normal(0.03, 0.01, size=300)
    take_every = rng.normal(0.005, 0.01, size=300)
    trader_v0 = rng.normal(0.01, 0.01, size=300)
    champion = rng.normal(0.015, 0.01, size=300)
    decision = promotion_gate(_m(cand), {"take_every": _m(take_every), "trader_v0": _m(trader_v0)}, _m(champion))
    assert decision.promote
    assert all(c.beats for c in decision.comparisons)
    assert any("champion" in r for r in decision.reasons)


def test_rejects_in_sample_only_winner():
    # Higher mean but huge variance -> difference CI straddles zero.
    rng = np.random.default_rng(1)
    cand = rng.normal(0.02, 0.5, size=200)
    trader_v0 = rng.normal(0.0, 0.01, size=200)
    take_every = rng.normal(0.0, 0.01, size=200)
    decision = promotion_gate(_m(cand), {"take_every": _m(take_every), "trader_v0": _m(trader_v0)}, None)
    assert not decision.promote


def test_rejects_when_worse_than_champion():
    rng = np.random.default_rng(2)
    cand = rng.normal(0.012, 0.01, size=300)
    take_every = rng.normal(0.0, 0.01, size=300)
    trader_v0 = rng.normal(0.005, 0.01, size=300)
    champion = rng.normal(0.03, 0.01, size=300)  # clearly better than candidate
    decision = promotion_gate(_m(cand), {"take_every": _m(take_every), "trader_v0": _m(trader_v0)}, _m(champion))
    assert not decision.promote
    champ = next(c for c in decision.comparisons if c.opponent == "champion")
    assert not champ.beats


def test_requires_known_baselines():
    with pytest.raises(KeyError):
        promotion_gate(_m([1.0, 2.0]), {"take_every": _m([0.0, 0.0])}, None)


def test_requires_returns_key():
    with pytest.raises(KeyError):
        promotion_gate({}, {"take_every": _m([0.0, 1.0]), "trader_v0": _m([0.0, 1.0])}, None)


def test_candidate_with_too_few_trades_holds_instead_of_crashing():
    """F-13 regression: insufficient evidence is a HOLD decision, never an exception."""
    from sgt.learning.promotion import promotion_gate

    base = {"returns": [0.01, -0.01, 0.0]}
    d = promotion_gate({"returns": [0.05]}, {"take_every": base, "trader_v0": base}, None)
    assert d.promote is False and "insufficient evidence" in d.reasons[0]


def test_opponent_without_trades_is_compared_against_not_trading():
    from sgt.learning.promotion import promotion_gate

    good = {"returns": [0.02, 0.03, 0.025, 0.02, 0.03] * 8}
    d = promotion_gate(good, {"take_every": {"returns": [0.0, 0.001] * 20}, "trader_v0": {"returns": []}}, None)
    v0 = next(c for c in d.comparisons if c.opponent == "trader_v0")
    assert v0.opponent_mean == 0.0 and v0.beats is True
    bad = {"returns": [0.02, -0.03] * 10}
    d2 = promotion_gate(bad, {"take_every": {"returns": [0.0, 0.001] * 20}, "trader_v0": {"returns": [0.1]}}, None)
    assert next(c for c in d2.comparisons if c.opponent == "trader_v0").beats is False
