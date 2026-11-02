"""Beta / volatility sizing layer (2026-11-02, docs/research/trader_ideas_design_2026-11-01.md §3.1).

Beta and volatility measure exposure, not expected profit, so they size positions and cap the
book; they never create a signal. Pure functions, no I/O:

- ``asset_risk``: Blume-adjusted beta vs a market series, R-squared, annualized vol, ATR%.
- ``size_book``: target sides/weights -> dollars, sized so each position risks the same share of
  equity at its stop (stop = ATR multiple), per-position cap, gross cap, then scaled down until
  |sum(beta x weight)| <= beta cap. Missing or stale risk inputs fail closed (no position).
- ``hedge_notional``: the index notional that neutralizes the book's beta (optional overlay).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

TRADING_DAYS = 252
BLUME_WEIGHT = 2 / 3          # adjusted beta = 2/3 * raw + 1/3 * 1 (Blume 1971; Bloomberg default)


@dataclass(frozen=True, slots=True)
class AssetRisk:
    symbol: str
    beta: float               # Blume-adjusted
    raw_beta: float
    r2: float
    ann_vol: float            # annualized std of daily returns
    atr_pct: float            # ATR(14) / last close
    n_obs: int


@dataclass(frozen=True, slots=True)
class SizingConfig:
    risk_per_trade: float = 0.005      # equity share lost if the stop is hit
    stop_atr_mult: float = 1.0         # stop distance = this x ATR%
    max_position_pct: float = 0.10     # |weight| cap per name
    gross_cap: float = 1.0             # sum |weight| cap (1.0 = no leverage)
    beta_cap: float = 1.0              # |sum beta x weight| cap
    min_obs: int = 60                  # fewer daily returns -> no position


@dataclass(slots=True)
class SizedBook:
    dollars: dict[str, float]
    weights: dict[str, float]
    portfolio_beta: float
    gross: float
    rejected: dict[str, str] = field(default_factory=dict)   # symbol -> reason
    notes: list[str] = field(default_factory=list)


def atr_pct(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> float:
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    atr = tr.iloc[-n:].mean()
    return float(atr / close.iloc[-1]) if len(close) > n and close.iloc[-1] > 0 else math.nan


def asset_risk(symbol: str, bars: pd.DataFrame, market_close: pd.Series, window: int = TRADING_DAYS) -> AssetRisk:
    """``bars``: daily OHLC indexed by date (adjusted); ``market_close``: index closes, same index type."""
    px = bars["close"].astype(float)
    joined = pd.concat([px.pct_change(), market_close.astype(float).pct_change()], axis=1, join="inner").dropna()
    joined = joined.iloc[-window:]
    n = len(joined)
    if n < 2:
        return AssetRisk(symbol, math.nan, math.nan, math.nan, math.nan, math.nan, n)
    y, x = joined.iloc[:, 0].to_numpy(), joined.iloc[:, 1].to_numpy()
    vx = float(np.var(x, ddof=1))
    raw = float(np.cov(y, x, ddof=1)[0, 1] / vx) if vx > 0 else math.nan
    r2 = float(np.corrcoef(y, x)[0, 1] ** 2) if vx > 0 and np.std(y) > 0 else math.nan
    vol = float(np.std(y, ddof=1) * math.sqrt(TRADING_DAYS))
    return AssetRisk(symbol, BLUME_WEIGHT * raw + (1 - BLUME_WEIGHT), raw, r2, vol,
                     atr_pct(bars["high"], bars["low"], px), n)


def size_book(targets: dict[str, float], risk: dict[str, AssetRisk], equity: float,
              cfg: SizingConfig = SizingConfig()) -> SizedBook:
    """``targets``: symbol -> signed conviction (+1 long, -1 short, fractions allowed, 0 = flat).

    Weight = sign x min(risk_per_trade / stop%, max_position_pct) x |conviction|, then gross and
    beta caps scale every weight by the same factor (relative sizing is preserved)."""
    if not equity > 0:
        raise ValueError("equity must be positive")
    w: dict[str, float] = {}
    rejected: dict[str, str] = {}
    for sym, conv in targets.items():
        if conv == 0:
            continue
        r = risk.get(sym)
        if r is None:
            rejected[sym] = "no risk inputs"
            continue
        if r.n_obs < cfg.min_obs or not all(map(math.isfinite, (r.beta, r.atr_pct))) or r.atr_pct <= 0:
            rejected[sym] = f"insufficient history (n={r.n_obs}) or invalid beta/ATR"
            continue
        stop = cfg.stop_atr_mult * r.atr_pct
        w[sym] = math.copysign(min(cfg.risk_per_trade / stop, cfg.max_position_pct) * min(abs(conv), 1.0), conv)
    notes = []
    gross = sum(abs(v) for v in w.values())
    if gross > cfg.gross_cap:
        k = cfg.gross_cap / gross
        w = {s: v * k for s, v in w.items()}
        notes.append(f"gross {gross:.2f} > cap {cfg.gross_cap:.2f}: scaled x{k:.3f}")
    pb = sum(v * risk[s].beta for s, v in w.items())
    if abs(pb) > cfg.beta_cap:
        k = cfg.beta_cap / abs(pb)
        w = {s: v * k for s, v in w.items()}
        notes.append(f"beta {pb:+.2f} > cap {cfg.beta_cap:.2f}: scaled x{k:.3f}")
        pb *= k
    return SizedBook(dollars={s: v * equity for s, v in w.items()}, weights=w, portfolio_beta=pb,
                     gross=sum(abs(v) for v in w.values()), rejected=rejected, notes=notes)


def hedge_notional(book: SizedBook, equity: float, hedge_beta: float = 1.0) -> float:
    """Signed index notional (negative = short) that brings the book's beta to zero."""
    if not hedge_beta > 0:
        raise ValueError("hedge_beta must be positive")
    return -book.portfolio_beta * equity / hedge_beta
