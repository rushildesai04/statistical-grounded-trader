"""End-to-end backtest behavior: trades with exit reasons, benchmarks,
no-look-ahead, next-bar fills, risk rejection, daily limits, broker-stop
gap-through, session-end + end-of-data flatten, and the performance budget."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import pytest

from sgt.core.types import Bar, Segment
from sgt.engine.backtest import default_risk_config, run_backtest
from sgt.engine.engine import StrategyEngine
from sgt.intraday.calendar import default_calendar
from sgt.intraday.synthetic import Ignition, make_multi_session
from sgt.risk.limits import LossBudgets, RiskConfig, RiskLimits
from sgt.strategy.loader import load_strategy
from sgt.strategy.runtime import EntryOverride
from sgt.strategy.schema import StrategyConfig

_ET_UTC_OFFSET = 4  # EDT in March

_CFG = load_strategy("tests/fixtures/trader_v0_mechanics.yaml")


def _sessions(n=3):
    return default_calendar().previous_sessions(date(2024, 3, 15), n)


def _dataset(symbols, *, seed=1, segments=(Segment.PRE, Segment.RTH, Segment.POST)):
    sess = _sessions(3)
    igs = (Ignition(start_minute=70, length=15, drift_per_bar=0.02, volume_mult=12.0),
           Ignition(start_minute=200, length=12, drift_per_bar=0.015, volume_mult=9.0))
    out = {}
    for i, sym in enumerate(symbols):
        out[sym] = make_multi_session(sym, sess, seed=seed + i, start_price=6.0 + i,
                                      segments=segments, ignitions=igs)
    return out


def test_end_to_end_trades_have_exit_reasons():
    data = _dataset(["AAAA"])
    res = run_backtest(_CFG, data, starting_cash=100_000.0, allow_unknown_float=True)
    assert res.summary["n_trades"] >= 1
    assert res.trades["exit_reason"].notna().all()
    assert set(res.trades["exit_reason"]) <= {
        "stop_loss_pct", "stop", "time_stop_minutes", "session_end", "session_end_forced",
        "carried_overnight", "macd_hist_state", "volume_exhaustion", "ema_cross_down",
        "psar_flip", "end_of_data"}
    # candidates recorded for taken AND skipped
    assert (res.candidates["taken"]).sum() >= 1
    assert (~res.candidates["taken"]).sum() >= 1


def test_take_every_benchmark_ge_trader_v0_and_random_is_seeded():
    data = _dataset(["AAAA", "BBBB"])
    base = run_backtest(_CFG, data, starting_cash=100_000.0, run_id="base", allow_unknown_float=True)
    te = run_backtest(_CFG, data, starting_cash=100_000.0,
                      entry_override=EntryOverride("take_every"), run_id="te", allow_unknown_float=True)
    assert te.summary["n_trades"] >= base.summary["n_trades"]
    r1 = run_backtest(_CFG, data, starting_cash=100_000.0,
                      entry_override=EntryOverride("random", rate=0.5, seed=7), run_id="r1", allow_unknown_float=True)
    r2 = run_backtest(_CFG, data, starting_cash=100_000.0,
                      entry_override=EntryOverride("random", rate=0.5, seed=7), run_id="r2", allow_unknown_float=True)
    assert r1.summary["n_trades"] == r2.summary["n_trades"]
    assert r1.summary["net_pnl"] == r2.summary["net_pnl"]


def test_no_look_ahead_perturbing_future_bars():
    bars = _dataset(["AAAA"])["AAAA"]
    cut = len(bars) // 2

    def decisions_up_to_cut(bar_list):
        eng = StrategyEngine(_CFG, allow_unknown_float=True)
        seen: dict = {}
        for b in bar_list:
            ds = eng.on_bar(b)
            if b.ts <= bars[cut].ts:
                seen[b.ts] = [(d.kind, d.reason, d.limit_price) for d in ds]
        return seen

    original = decisions_up_to_cut(bars)
    perturbed = list(bars)
    for i in range(cut + 1, len(perturbed)):
        b = perturbed[i]
        perturbed[i] = Bar(symbol=b.symbol, ts=b.ts, open=b.open * 1.5, high=b.high * 1.8,
                           low=b.low * 1.2, close=b.close * 1.6, volume=b.volume * 3,
                           session_date=b.session_date, minute_of_day=b.minute_of_day,
                           segment=b.segment)
    assert decisions_up_to_cut(perturbed) == original


def test_fills_happen_strictly_after_decision_bar():
    from sgt.engine.eventlog import InMemoryEventSink
    sink = InMemoryEventSink()
    run_backtest(_CFG, _dataset(["AAAA"]), starting_cash=100_000.0, event_sink=sink, allow_unknown_float=True)
    orders = {e["payload"]["client_order_id"]: e["ts"] for e in sink.events if e["kind"] == "order"}
    fills = [e for e in sink.events if e["kind"] == "fill"]
    assert fills, "expected at least one fill"
    for f in fills:
        cid = f["payload"]["client_order_id"]
        assert f["ts"] > orders[cid], "a fill must occur on a later bar than its order"


def test_determinism_byte_identical_jsonl(tmp_path):
    from sgt.engine.eventlog import JsonlEventSink
    data = _dataset(["AAAA", "BBBB"])
    p1, p2 = tmp_path / "run1.jsonl", tmp_path / "run2.jsonl"
    for p in (p1, p2):
        sink = JsonlEventSink(p)
        run_backtest(_CFG, data, starting_cash=100_000.0, event_sink=sink, run_id="x", allow_unknown_float=True)
        sink.close()
    assert p1.read_bytes() == p2.read_bytes()


def test_risk_rejection_is_journaled_as_skip():
    rc = default_risk_config(_CFG)
    rc = rc.model_copy(update={"limits": rc.limits.model_copy(update={"min_price": 1.0e6})})
    res = run_backtest(_CFG, _dataset(["AAAA"]), starting_cash=100_000.0,
                       entry_override=EntryOverride("take_every"), risk_config=rc, allow_unknown_float=True)
    assert res.summary["n_trades"] == 0  # every entry rejected by min_price
    risk_skips = res.candidates[(~res.candidates["taken"]) & (res.candidates["skip_reason"] == "risk")]
    assert len(risk_skips) >= 1
    reasons = [r for e in res.events.events if e["kind"] == "risk_reject"
               for r in e["payload"]["reasons"]]
    assert "min_price" in reasons


def test_daily_loss_limit_blocks_further_entries():
    rc = default_risk_config(_CFG)
    rc = rc.model_copy(update={"limits": rc.limits.model_copy(
        update={"daily_loss_limit_global": 1.0, "daily_loss_limit_per_account": 1.0})})
    res = run_backtest(_CFG, _dataset(["AAAA"]), starting_cash=100_000.0,
                       entry_override=EntryOverride("take_every"), risk_config=rc, allow_unknown_float=True)
    reasons = [r for e in res.events.events if e["kind"] == "risk_reject"
               for r in e["payload"]["reasons"]]
    assert any("daily_loss_limit" in r for r in reasons)


def _rth_bar(sym, i, close, *, high=None, low=None, vol=50_000.0, minute=600):
    high = high if high is not None else close + 0.05
    low = low if low is not None else close - 0.05
    et_minutes = minute
    hh, mm = divmod(et_minutes, 60)
    ts = datetime(2024, 3, 12, hh + _ET_UTC_OFFSET, mm, tzinfo=timezone.utc)
    return Bar(symbol=sym, ts=ts, open=close, high=high, low=low, close=close, volume=vol,
               session_date=date(2024, 3, 12), minute_of_day=minute, segment=Segment.RTH)


def _decline_then_rise(decline_n, rise_n, rise_step=0.25):
    closes = [10.0 - 0.03 * i for i in range(decline_n)]
    last = closes[-1]
    closes += [last + rise_step * i for i in range(1, rise_n + 1)]
    return closes


def test_broker_stop_fills_on_gap_through():
    # Decline then rise so the EMA cross fires AFTER warmup (a monotonic ramp never
    # re-crosses); a deep gap-down bar then forces the broker STOP to fill at the open.
    closes = _decline_then_rise(28, 10)
    closes += [8.0]  # crash below the ~3% protective stop
    # Wide low wick keeps the marketable BUY limit fillable on a trending bar.
    bars = [_rth_bar("AAAA", i, c, low=c - 0.3, high=c + 0.1, minute=600 + i)
            for i, c in enumerate(closes)]
    bars[-1] = _rth_bar("AAAA", len(closes) - 1, 8.0, high=8.1, low=7.8, minute=600 + len(closes) - 1)
    res = run_backtest(_CFG, {"AAAA": bars}, starting_cash=100_000.0,
                       entry_override=EntryOverride("take_every"), allow_unknown_float=True)
    stop_orders = [e for e in res.events.events
                   if e["kind"] == "order" and e["payload"]["order_type"] == "stop"]
    assert stop_orders, "a protective STOP must be placed after the entry fill"
    assert "stop" in set(res.trades["exit_reason"]) or "stop_loss_pct" in set(res.trades["exit_reason"])


def test_positions_open_at_end_are_closed_end_of_data():
    # Short rise after the cross so no stop/time-stop fires before data ends.
    closes = _decline_then_rise(28, 12)
    bars = [_rth_bar("AAAA", i, c, low=c - 0.3, high=c + 0.1, minute=600 + i)
            for i, c in enumerate(closes)]
    res = run_backtest(_CFG, {"AAAA": bars}, starting_cash=100_000.0,
                       entry_override=EntryOverride("take_every"), allow_unknown_float=True)
    assert "end_of_data" in set(res.trades["exit_reason"])


def test_performance_20_symbols_3_sessions_under_20s():
    data = _dataset([f"SYM{i:02d}" for i in range(20)], seed=5)
    n_bars = sum(len(v) for v in data.values())
    t0 = time.perf_counter()
    res = run_backtest(_CFG, data, starting_cash=1_000_000.0, run_id="perf", allow_unknown_float=True)
    elapsed = time.perf_counter() - t0
    assert elapsed < 20.0, f"{n_bars} bars took {elapsed:.1f}s"
    assert res.summary["n_trades"] >= 0
