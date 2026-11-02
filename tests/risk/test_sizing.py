"""Beta/volatility sizing (src/sgt/risk/sizing.py)."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from sgt.risk.sizing import AssetRisk, SizingConfig, asset_risk, hedge_notional, size_book


def bars_from_returns(r: np.ndarray, spread: float) -> pd.DataFrame:
    idx = pd.bdate_range("2025-01-02", periods=len(r) + 1)
    c = 100 * np.cumprod(np.r_[1.0, 1 + r])
    return pd.DataFrame({"close": c, "high": c * (1 + spread), "low": c * (1 - spread)}, index=idx)


def risk(sym: str, beta: float, atr: float, n: int = 252) -> AssetRisk:
    return AssetRisk(sym, beta, beta, 0.5, atr * 16, atr, n)


def test_asset_risk_recovers_beta_and_applies_blume():
    rng = np.random.default_rng(7)
    m = rng.normal(0, 0.01, 400)
    stock = bars_from_returns(3.0 * m + rng.normal(0, 0.002, 400), 0.02)
    mkt = bars_from_returns(m, 0.005).close
    r = asset_risk("X", stock, mkt)
    assert r.n_obs == 252
    assert r.raw_beta == pytest.approx(3.0, abs=0.1)
    assert r.beta == pytest.approx(2 / 3 * r.raw_beta + 1 / 3)
    assert r.r2 > 0.9 and r.atr_pct > 0.03


def test_volatile_name_gets_proportionally_fewer_dollars():
    cfg = SizingConfig(risk_per_trade=0.005, max_position_pct=0.5, gross_cap=5, beta_cap=10)
    book = size_book({"CALM": 1, "WILD": 1}, {"CALM": risk("CALM", 1.0, 0.02), "WILD": risk("WILD", 1.0, 0.08)},
                     100_000, cfg)
    assert book.dollars["CALM"] == pytest.approx(25_000)          # 0.5% / 2% stop
    assert book.dollars["WILD"] == pytest.approx(6_250)           # a quarter: 0.5% / 8% stop


def test_beta_cap_scales_book_uniformly_and_shorts_offset():
    cfg = SizingConfig(risk_per_trade=0.01, max_position_pct=0.5, gross_cap=5, beta_cap=0.5)
    rk = {"A": risk("A", 4.0, 0.02), "B": risk("B", 1.0, 0.02)}
    book = size_book({"A": 1, "B": 1}, rk, 100_000, cfg)
    assert abs(book.portfolio_beta) == pytest.approx(0.5)
    assert book.weights["A"] == pytest.approx(book.weights["B"])  # relative sizing kept
    hedged = size_book({"A": 1, "B": -1}, rk, 100_000, cfg)
    assert hedged.portfolio_beta == pytest.approx((4.0 - 1.0) * hedged.weights["A"])


def test_missing_or_short_history_fails_closed():
    rk = {"NEW": risk("NEW", 2.0, 0.05, n=20), "NAN": risk("NAN", math.nan, 0.05)}
    book = size_book({"NEW": 1, "NAN": 1, "GHOST": -1}, rk, 50_000)
    assert book.dollars == {}
    assert set(book.rejected) == {"NEW", "NAN", "GHOST"}


def test_gross_cap_and_hedge():
    cfg = SizingConfig(risk_per_trade=0.01, max_position_pct=0.4, gross_cap=1.0, beta_cap=10)
    rk = {s: risk(s, 2.0, 0.01) for s in "ABC"}
    book = size_book({s: 1 for s in "ABC"}, rk, 10_000, cfg)
    assert book.gross == pytest.approx(1.0)
    assert hedge_notional(book, 10_000, hedge_beta=1.0) == pytest.approx(-2.0 * 10_000)
    with pytest.raises(ValueError):
        size_book({"A": 1}, rk, 0)
