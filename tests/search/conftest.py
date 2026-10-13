"""Shared builders for sgt.search tests.

All synthetic sessions use REAL XNYS trading days: the backtester's risk gate rejects
any entry whose bar falls outside an allowed named window ("window_not_allowed"), and
``SessionCalendar.windows`` only returns rth/power_hour/... for genuine sessions.
"""
from __future__ import annotations

from datetime import date

import exchange_calendars as xcals
import pytest
import yaml

from sgt.intraday.synthetic import Ignition, make_multi_session
from sgt.strategy.schema import StrategyConfig
from sgt.strategy.validate import validate_strategy

_IGNITIONS = (
    Ignition(start_minute=40, length=18, drift_per_bar=0.010, volume_mult=8.0),
    Ignition(start_minute=150, length=18, drift_per_bar=0.012, volume_mult=8.0),
    Ignition(start_minute=300, length=18, drift_per_bar=0.014, volume_mult=8.0),
)


def trading_days(n: int, start: str = "2025-09-02") -> list[date]:
    cal = xcals.get_calendar("XNYS")
    sessions = cal.sessions_in_range(start, "2025-12-15")
    return [ts.date() for ts in sessions[:n]]


def simple_config_dict(*, cid: str = "t_simple", fast: int = 9, slow: int = 24,
                       time_stop: int = 5, confirm_value: float = 100.0) -> dict:
    return {
        "id": cid,
        "version": 1,
        "universe": {"float_max": 1.0e12, "price_min": 0.01, "price_max": 1.0e9,
                     "rel_vol_min": 0.0, "spread_max_pct": 100.0},
        "trigger": {"kind": "ema_cross_up", "fast": fast, "slow": slow},
        "confirm": {"k_of_m": 1, "signals": [{"kind": "rsi_lt", "n": 14, "value": confirm_value}]},
        "exits": {"any_of": [
            {"kind": "stop_loss_pct", "pct": 50.0},
            {"kind": "time_stop_minutes", "n": time_stop},
        ]},
        "sizing": {"method": "fixed_risk", "risk_per_trade_pct_of_allocation": 0.5,
                   "allocation_usd": 100000.0, "max_notional_usd": 1.0e9,
                   "entry_offset_bps": 0.0, "exit_offset_bps": 0.0},
    }


@pytest.fixture
def make_config():
    def _make(**kw) -> StrategyConfig:
        return validate_strategy(simple_config_dict(**kw))
    return _make


@pytest.fixture
def make_bars():
    def _make(symbol: str = "AAA", n_sessions: int = 6, seed: int = 1):
        days = trading_days(n_sessions)
        bars = make_multi_session(symbol, days, start_price=5.0,
                                  ignitions=_IGNITIONS, seed=seed)
        return bars, days
    return _make


@pytest.fixture
def bars_by_symbol(make_bars):
    bars, days = make_bars(symbol="AAA", n_sessions=6, seed=1)
    return {"AAA": bars}, days


@pytest.fixture
def base_yaml(tmp_path):
    path = tmp_path / "base.yaml"
    path.write_text(yaml.safe_dump(simple_config_dict()), encoding="utf-8")
    return str(path)


@pytest.fixture
def baseline_yaml(tmp_path):
    path = tmp_path / "baseline_v0.yaml"
    path.write_text(yaml.safe_dump(simple_config_dict(cid="baseline_v0", fast=8, slow=21)),
                    encoding="utf-8")
    return str(path)
