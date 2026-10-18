"""MeasuredCostModel: point-in-time lookup (no look-ahead), flagged fallback, stress
multiplier, and drop-in compatibility with the simulation engine (a real backtest)."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from sgt.brokers.costs import DEFAULT_COST_MODEL
from sgt.brokers.measured_costs import MeasuredCostModel
from sgt.core.types import Bar, Segment, Side
from sgt.engine.backtest import run_backtest
from sgt.intraday.quotes import SpreadSample
from sgt.strategy.runtime import EntryOverride
from sgt.strategy.schema import StrategyConfig

UTC = timezone.utc
ET = ZoneInfo("America/New_York")


def _sample(sym: str, d: date, minute: int, rel: float, n: int = 100) -> SpreadSample:
    return SpreadSample(sym, d, minute, "rth", rel, n, mid_price=10.0)


def _ts(d: date, hh: int, mm: int) -> datetime:
    return datetime.combine(d, time(hh, mm), ET).astimezone(UTC)


def test_point_in_time_uses_only_prior_sessions():
    prior = date(2024, 3, 11)
    future = date(2024, 3, 15)
    query_day = date(2024, 3, 12)
    # 3 prior-session opening-bucket samples @ 0.004 full spread -> 0.002 half.
    samples = [_sample("WFF", prior, 600, 0.004) for _ in range(3)]
    # a future session with an enormous spread that must be ignored (no look-ahead).
    samples += [_sample("WFF", future, 600, 0.50) for _ in range(5)]
    m = MeasuredCostModel(samples, min_samples=3)
    rt, source = m.round_trip("WFF", _ts(query_day, 10, 0), price=10.0)
    assert source == "measured"
    assert abs(rt - 2 * 0.002) < 1e-9  # future 0.50 sample had no effect


def test_fallback_flagged_when_too_few_samples():
    samples = [_sample("WFF", date(2024, 3, 11), 600, 0.004) for _ in range(2)]  # < min_samples
    m = MeasuredCostModel(samples, min_samples=3)
    rt, source = m.round_trip("WFF", _ts(date(2024, 3, 12), 10, 0), price=10.0)
    assert source == "model_fallback"
    assert abs(rt - 2 * DEFAULT_COST_MODEL.half_spread_pct(10.0)) < 1e-12


def test_fallback_for_unknown_symbol():
    m = MeasuredCostModel([], min_samples=1)
    rt, source = m.round_trip("ZZZZ", _ts(date(2024, 3, 12), 10, 0), price=3.0)
    assert source == "model_fallback"
    assert abs(rt - 2 * DEFAULT_COST_MODEL.half_spread_pct(3.0)) < 1e-12


def test_nearest_bucket_fallback_across_time_of_day():
    # Only opening-bucket samples exist; a midday query should still resolve to them
    # (nearest time-of-day bucket) rather than fall back to the model.
    samples = [_sample("WFF", date(2024, 3, 11), 600, 0.004) for _ in range(3)]
    m = MeasuredCostModel(samples, min_samples=3)
    rt, source = m.round_trip("WFF", _ts(date(2024, 3, 12), 12, 30), price=10.0)
    assert source == "measured"
    assert abs(rt - 2 * 0.002) < 1e-9


def test_stress_multiplier_scales_both_sources():
    samples = [_sample("WFF", date(2024, 3, 11), 600, 0.004) for _ in range(3)]
    m = MeasuredCostModel(samples, min_samples=3)
    base_rt, _ = m.round_trip("WFF", _ts(date(2024, 3, 12), 10, 0), 10.0)
    up = m.stress(1.5)
    down = m.stress(0.5)
    up_rt, up_src = up.round_trip("WFF", _ts(date(2024, 3, 12), 10, 0), 10.0)
    down_rt, _ = down.round_trip("WFF", _ts(date(2024, 3, 12), 10, 0), 10.0)
    assert up_src == "measured"
    assert abs(up_rt - base_rt * 1.5) < 1e-12
    assert abs(down_rt - base_rt * 0.5) < 1e-12
    assert abs(up.stress_mult - 1.5) < 1e-12
    # stress compounds
    assert abs(up.stress(2.0).stress_mult - 3.0) < 1e-12


def test_half_spread_and_slippage_measured_path():
    # measured 0.001 full -> 0.0005 half, below the $5-20 bucket's 0.0020 half-spread.
    samples = [_sample("WFF", date(2024, 3, 11), 600, 0.001) for _ in range(3)]
    m = MeasuredCostModel(samples, min_samples=3)
    ts = _ts(date(2024, 3, 12), 10, 0)
    assert abs(m.half_spread_pct(10.0, symbol="WFF", ts=ts) - 0.0005) < 1e-9
    # without symbol/ts it must match the bucket model (SimBroker's call shape)
    assert abs(m.half_spread_pct(10.0) - DEFAULT_COST_MODEL.half_spread_pct(10.0)) < 1e-12
    measured_slip = m.slippage(10.0, Side.BUY, 100, 10_000.0, symbol="WFF", ts=ts)
    model_slip = m.slippage(10.0, Side.BUY, 100, 10_000.0)
    assert measured_slip < model_slip  # measured 0.05% half-spread < bucket 0.20%
    assert measured_slip > 0 and model_slip > 0


# --- engine compatibility --------------------------------------------------
def _ramp_cfg() -> StrategyConfig:
    return StrategyConfig.model_validate({
        "id": "mc", "universe": {"float_max": 1e9, "price_min": 0.1},
        "trigger": {"kind": "ema_cross_up", "fast": 9, "slow": 24},
        "confirm": {"k_of_m": 1, "signals": [{"kind": "pvt_state", "state": "above_signal"}]},
        "exits": {"any_of": [
            {"kind": "stop_loss_pct", "pct": 3.0},
            {"kind": "time_stop_minutes", "n": 30},
            {"kind": "session_end", "minutes_before_close": 5, "window": "rth"}]},
        "sizing": {"risk_per_trade_pct_of_allocation": 0.5,
                   "allocation_usd": 10_000.0, "max_notional_usd": 20_000.0},
    })


def _ramp_bars(d: date) -> list[Bar]:
    # decline then rise: an EMA-cross-up entry on a liquid RTH session (mirrors the
    # engine's own backtest fixtures), then a time/session exit closes it.
    closes = [10.0 - 0.03 * i for i in range(28)]
    closes += [closes[-1] + 0.25 * i for i in range(1, 11)]
    bars: list[Bar] = []
    for k, c in enumerate(closes):
        minute = 575 + k
        hh, mm = divmod(minute, 60)
        ts = datetime.combine(d, time(hh, mm), ET).astimezone(UTC)
        bars.append(Bar("AAAA", ts, c, c + 0.3, c - 0.3, c, 50_000.0, d, minute, Segment.RTH))
    return bars


def test_backtest_runs_with_measured_cost_model():
    d = date(2024, 3, 12)
    cm = MeasuredCostModel([_sample("AAAA", date(2024, 3, 11), 600, 0.004) for _ in range(3)],
                           min_samples=3)
    res = run_backtest(_ramp_cfg(), {"AAAA": _ramp_bars(d)}, starting_cash=100_000.0,
                       cost_model=cm, entry_override=EntryOverride("take_every"),
                       run_id="mc", allow_unknown_float=True)
    # the backtest completes and the cost model's fill path was exercised.
    assert res.summary["n_trades"] >= 1
    assert not [e for e in res.events.events if e["kind"] == "invariant_violation"]
