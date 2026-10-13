"""F-8 regression: the strategy universe (price band, float cap, cumulative rel-vol) is a
hard entry gate in every mode, and an unknown float fails closed unless explicitly allowed."""
from __future__ import annotations

from datetime import date

from sgt.core.interfaces import ReferenceData
from sgt.core.types import Segment
from sgt.engine.backtest import run_backtest
from sgt.intraday.calendar import default_calendar
from sgt.intraday.synthetic import Ignition, make_multi_session
from sgt.strategy.loader import load_strategy
from sgt.strategy.runtime import EntryOverride

_MECH = load_strategy("tests/fixtures/trader_v0_mechanics.yaml")
_V0 = load_strategy("strategies/trader_v0.yaml")
_TAKE = EntryOverride(mode="take_every")


def _data(sym="UNI", start_price=6.0):
    sess = default_calendar().previous_sessions(date(2024, 3, 15), 3)
    igs = (Ignition(start_minute=70, length=15, drift_per_bar=0.02, volume_mult=12.0),)
    return {sym: make_multi_session(sym, sess, seed=7, start_price=start_price,
                                    segments=(Segment.RTH,), ignitions=igs)}


def _skips(res):
    return set(res.candidates["skip_reason"].dropna())


def _ref(sym, free_float):
    return {sym: ReferenceData(symbol=sym, as_of=date(2024, 3, 13), free_float=free_float)}


def test_unknown_float_fails_closed_by_default():
    res = run_backtest(_MECH, _data(), starting_cash=100_000.0, entry_override=_TAKE)
    assert res.summary["n_trades"] == 0
    assert "universe:float_unknown" in _skips(res)


def test_allow_unknown_float_admits_symbol():
    res = run_backtest(_MECH, _data(), starting_cash=100_000.0, entry_override=_TAKE,
                       allow_unknown_float=True)
    assert res.summary["n_trades"] >= 1


def test_float_above_cap_is_excluded_even_for_benchmarks():
    big = _MECH.universe.float_max * 10
    res = run_backtest(_MECH, _data(), starting_cash=100_000.0, entry_override=_TAKE,
                       reference=_ref("UNI", big))
    assert res.summary["n_trades"] == 0 and "universe:float" in _skips(res)
    small = run_backtest(_MECH, _data(), starting_cash=100_000.0, entry_override=_TAKE,
                         reference=_ref("UNI", 1_000_000))
    assert small.summary["n_trades"] >= 1


def test_price_outside_band_is_excluded():
    # trader_v0's band is [0.50, 100]; a $250 synthetic stock must never be entered.
    res = run_backtest(_V0, _data(start_price=250.0), starting_cash=1_000_000.0, entry_override=_TAKE,
                       reference=_ref("UNI", 1_000_000))
    assert res.summary["n_trades"] == 0 and "universe:price" in _skips(res)


def test_cumulative_rel_vol_floor_gates_entries():
    # Identical ignition timing every session keeps cumulative rel-vol near 1 < 3.0.
    res = run_backtest(_V0, _data(), starting_cash=100_000.0, entry_override=_TAKE,
                       reference=_ref("UNI", 1_000_000))
    assert res.summary["n_trades"] == 0 and "universe:rel_vol" in _skips(res)
