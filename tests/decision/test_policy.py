"""Policy tests (DECISION_MODEL.md §7, §10 F7): decision rule, hurdle, sizing, cap."""
from __future__ import annotations

import pytest

from sgt.decision.models import ModelPrediction
from sgt.decision.policy import (
    EnsembleModelPolicy,
    PolicyDecision,
    PolicyParams,
    lower_ci_probability,
    shrink_probability,
    sized_returns,
)


class _StubModel:
    def __init__(self, mu, sigma, p, b, contribs=(("f_sig", 1.2),)):
        self._pred = ModelPrediction(mu, sigma, p, b, list(contribs))

    def predict(self, features):
        return self._pred


def _ctx(mark=100.0, atr=1.0, rt=0.004, open_risk=0.0):
    return {"mark_price": mark, "atr": atr, "round_trip_cost": rt, "open_risk": open_risk}


def _params(**kw):
    base = dict(k_tp=2.0, k_sl=1.0, n_eff=250, z=1.0, h=0.0, p_min=0.5,
                f_max=0.02, f_fixed=0.005, kelly_n_threshold=1000, portfolio_cap=0.06)
    base.update(kw)
    return PolicyParams(**base)


def test_decision_field_names():
    d = PolicyDecision(True, 0.01, 99.0, 102.0, "ok", [("f", 1.0)])
    assert (d.trade, d.size_fraction, d.stop_price, d.take_profit_price) == (True, 0.01, 99.0, 102.0)
    assert d.reason == "ok" and d.contributions == [("f", 1.0)]


def test_trade_when_edge_and_prob_pass():
    pol = EnsembleModelPolicy(_StubModel(mu=0.02, sigma=0.005, p=0.8, b=1.5), _params())
    d = pol.decide({}, _ctx())
    assert d.trade and d.size_fraction == pytest.approx(0.005)  # fixed-fractional (N<1000)
    assert d.stop_price == pytest.approx(99.0) and d.take_profit_price == pytest.approx(102.0)
    assert d.contributions == [("f_sig", 1.2)]


def test_no_trade_when_edge_below_hurdle():
    # h_eff = max(h, 0.5*rt) = 0.5*0.01 = 0.005; edge = 0.004 - 1*0.001 = 0.003 < 0.005.
    pol = EnsembleModelPolicy(_StubModel(mu=0.004, sigma=0.001, p=0.9, b=2.0), _params())
    d = pol.decide({}, _ctx(rt=0.01))
    assert not d.trade and "edge" in d.reason and "h_eff" in d.reason


def test_hurdle_uses_measured_round_trip():
    model = _StubModel(mu=0.006, sigma=0.0, p=0.9, b=2.0)
    assert EnsembleModelPolicy(model, _params()).decide({}, _ctx(rt=0.004)).trade  # h_eff=0.002
    assert not EnsembleModelPolicy(model, _params()).decide({}, _ctx(rt=0.02)).trade  # h_eff=0.01


def test_no_trade_when_prob_low():
    pol = EnsembleModelPolicy(_StubModel(mu=0.05, sigma=0.001, p=0.5, b=2.0),
                              _params(p_min=0.7))
    d = pol.decide({}, _ctx())
    assert not d.trade and "p_lowCI" in d.reason


def test_fixed_fractional_below_threshold_vs_kelly_above():
    model = _StubModel(mu=0.05, sigma=0.001, p=0.85, b=2.0)
    small = EnsembleModelPolicy(model, _params(n_eff=250)).decide({}, _ctx())
    big = EnsembleModelPolicy(model, _params(n_eff=5000)).decide({}, _ctx())
    assert small.size_fraction == pytest.approx(0.005)      # fixed fractional
    assert big.size_fraction != pytest.approx(0.005)        # Kelly-derived
    assert 0.0 < big.size_fraction <= 0.02                  # clipped to f_max


def test_kelly_clipped_to_f_max():
    pol = EnsembleModelPolicy(_StubModel(mu=0.5, sigma=0.0, p=0.99, b=5.0),
                              _params(n_eff=5000, f_max=0.015))
    assert pol.decide({}, _ctx()).size_fraction == pytest.approx(0.015)


def test_portfolio_at_risk_cap():
    model = _StubModel(mu=0.05, sigma=0.001, p=0.85, b=2.0)
    # headroom = cap(0.06) - open_risk(0.058) = 0.002 < f_fixed(0.005) -> clipped.
    d = EnsembleModelPolicy(model, _params()).decide({}, _ctx(open_risk=0.058))
    assert d.trade and d.size_fraction == pytest.approx(0.002)
    # cap fully consumed -> NO-TRADE.
    d2 = EnsembleModelPolicy(model, _params()).decide({}, _ctx(open_risk=0.06))
    assert not d2.trade and "portfolio at-risk cap" in d2.reason


def test_shrink_and_lower_ci_monotone():
    assert shrink_probability(0.9, 10, 50.0) < shrink_probability(0.9, 10000, 50.0)
    assert lower_ci_probability(0.8, 100, 50.0, 1.0) < shrink_probability(0.8, 100, 50.0)


def test_sized_returns_only_traded():
    decs = [PolicyDecision(True, 0.01, None, None, ""), PolicyDecision(False, 0.0, None, None, "")]
    assert sized_returns(decs, [0.5, -0.9]) == [pytest.approx(0.005)]
