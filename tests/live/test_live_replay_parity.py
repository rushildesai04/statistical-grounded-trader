"""Replay-equals-live parity: a SHADOW LiveExecutor over the same bars must produce
the identical decision/order/fill/reject sequence as run_backtest (plan §3.1)."""
from __future__ import annotations

from datetime import date

from sgt.core.types import Segment
from sgt.engine.backtest import default_risk_config, run_backtest
from sgt.engine.eventlog import InMemoryEventSink
from sgt.intraday.calendar import default_calendar
from sgt.intraday.synthetic import Ignition, make_multi_session
from sgt.live.replay import assert_replay_parity, parity_events, replay_session
from sgt.strategy.loader import load_strategy
from sgt.strategy.runtime import EntryOverride

_CFG = load_strategy("tests/fixtures/trader_v0_mechanics.yaml")


def _dataset(symbols, *, seed=1):
    sess = default_calendar().previous_sessions(date(2024, 3, 15), 3)
    igs = (Ignition(start_minute=70, length=15, drift_per_bar=0.02, volume_mult=12.0),
           Ignition(start_minute=200, length=12, drift_per_bar=0.015, volume_mult=9.0))
    out = {}
    for i, sym in enumerate(symbols):
        out[sym] = make_multi_session(sym, sess, seed=seed + i, start_price=6.0 + i,
                                      segments=(Segment.PRE, Segment.RTH, Segment.POST), ignitions=igs)
    return out


async def test_shadow_matches_backtest_single_symbol():
    data = _dataset(["AAAA"])
    bt_sink = InMemoryEventSink()
    bt = run_backtest(_CFG, data, starting_cash=100_000.0, event_sink=bt_sink, run_id="bt", allow_unknown_float=True)
    sh = await replay_session(_CFG, data, starting_cash=100_000.0, run_id="sh", allow_unknown_float=True)
    assert_replay_parity(bt_sink, sh.events)
    # A meaningful run: there were orders and fills to agree on.
    assert any(k == "order" for k, _ts, _p in parity_events(bt_sink))
    assert len(bt.trades) == len(sh.trades)


async def test_shadow_matches_backtest_multi_symbol_take_every():
    data = _dataset(["AAAA", "BBBB"], seed=3)
    bt_sink = InMemoryEventSink()
    run_backtest(_CFG, data, starting_cash=100_000.0, event_sink=bt_sink, run_id="bt",
                 entry_override=EntryOverride("take_every"), allow_unknown_float=True)
    sh = await replay_session(_CFG, data, starting_cash=100_000.0, run_id="sh",
                              entry_override=EntryOverride("take_every"), allow_unknown_float=True)
    assert_replay_parity(bt_sink, sh.events)


async def test_shadow_matches_backtest_tight_daily_limit():
    # Exercise the risk_reject path so parity covers rejected orders too.
    data = _dataset(["AAAA"])
    rc = default_risk_config(_CFG)
    rc = rc.model_copy(update={"limits": rc.limits.model_copy(
        update={"daily_loss_limit_global": 1.0, "daily_loss_limit_per_account": 1.0})})
    bt_sink = InMemoryEventSink()
    run_backtest(_CFG, data, starting_cash=100_000.0, event_sink=bt_sink, run_id="bt",
                 entry_override=EntryOverride("take_every"), risk_config=rc, allow_unknown_float=True)
    sh = await replay_session(_CFG, data, starting_cash=100_000.0, run_id="sh",
                              entry_override=EntryOverride("take_every"), risk_config=rc, allow_unknown_float=True)
    assert_replay_parity(bt_sink, sh.events)
    assert any(k == "risk_reject" for k, _ts, _p in parity_events(bt_sink))
