"""Runtime state-machine transitions: entry, entry-timeout cancel, partial fills,
the halted overlay blocking new entries, and stop-driven exit."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sgt.core.types import Bar, Fill, Segment, Side
from sgt.strategy.base import EvalView, HistoryView
from sgt.strategy.runtime import EntryOverride, State, StrategyRuntime
from sgt.strategy.schema import StrategyConfig

_FK, _SK = "ema(n=9).value", "ema(n=24).value"


def _config() -> StrategyConfig:
    return StrategyConfig.model_validate({
        "id": "t", "universe": {"float_max": 1e9, "price_min": 0.1},
        "trigger": {"kind": "ema_cross_up", "fast": 9, "slow": 24},
        "confirm": {"k_of_m": 1, "signals": [{"kind": "pvt_state", "state": "above_signal"}]},
        "exits": {"any_of": [{"kind": "stop_loss_pct", "pct": 3.0},
                             {"kind": "time_stop_minutes", "n": 30}]},
        "sizing": {"risk_per_trade_pct_of_allocation": 0.5,
                   "allocation_usd": 10_000.0, "max_notional_usd": 20_000.0},
    })


_T0 = datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc)


def _bar(i, close=10.0, high=None, low=None) -> Bar:
    high = high if high is not None else close + 0.2
    low = low if low is not None else close - 0.2
    return Bar(symbol="AAAA", ts=_T0 + timedelta(minutes=i), open=close, high=high, low=low,
               close=close, volume=1000.0, session_date=date(2024, 3, 12),
               minute_of_day=900 + i, segment=Segment.RTH)


def _view(bar, now, prev):
    return EvalView(bar=bar, snap_now=now, snap_prev=prev, windows=frozenset({"rth"}),
                    history=HistoryView([], [], [], [], []), position=None)


def _cross_up():
    return {_FK: 10.1, _SK: 10.0}, {_FK: 9.9, _SK: 10.0}


def _no_cross():
    return {_FK: 10.1, _SK: 10.0}, {_FK: 10.1, _SK: 10.0}


def _runtime(**kw):
    cands = []
    rt = StrategyRuntime(_config(), "AAAA", candidate_sink=cands.append, **kw, allow_unknown_float=True)
    return rt, cands


def test_trigger_enters_via_take_every_and_sizes():
    rt, _ = _runtime(entry_override=EntryOverride("take_every"))
    now, prev = _cross_up()
    decisions = rt.on_bar(_bar(0), now, _view(_bar(0), now, prev))
    assert len(decisions) == 1
    d = decisions[0]
    assert d.kind == "entry" and d.qty_hint >= 1 and d.stop_price < d.limit_price
    assert rt.state is State.ENTRY_PENDING


def test_confirm_failure_skips_and_records_candidate():
    rt, cands = _runtime()  # no override: confirm pvt_state must pass
    now, prev = _cross_up()
    now = {**now, "pvt(signal_kind=sma,signal_n=14).pvt": 1.0,
           "pvt(signal_kind=sma,signal_n=14).signal": 2.0}  # pvt below signal -> confirm fails
    decisions = rt.on_bar(_bar(0), now, _view(_bar(0), now, prev))
    assert decisions == []
    assert cands and cands[-1].skip_reason == "confirm" and cands[-1].taken is False


def test_reset_entry_and_enter_exit_pending_transitions():
    # Entry-timeout now lives in OrderLifecycle; the runtime exposes the callbacks it
    # drives. reset_entry rolls ENTRY_PENDING back; enter_exit_pending latches exiting.
    rt, _ = _runtime(entry_override=EntryOverride("take_every"))
    now, prev = _cross_up()
    rt.on_bar(_bar(0), now, _view(_bar(0), now, prev))
    assert rt.state is State.ENTRY_PENDING
    rt.reset_entry()
    assert rt.state not in (State.ENTRY_PENDING, State.IN_POSITION, State.EXIT_PENDING)
    # drive into a position, then force exiting without the runtime emitting the exit
    rt.on_bar(_bar(1), now, _view(_bar(1), now, prev))
    rt.on_fill(Fill("c", "b", "acct", "AAAA", Side.BUY, 100, 10.0, _bar(2).ts, "f0"))
    assert rt.state is State.IN_POSITION
    rt.enter_exit_pending()
    assert rt.state is State.EXIT_PENDING


def test_partial_fills_accumulate_then_exit_on_stop():
    rt, _ = _runtime(entry_override=EntryOverride("take_every"))
    now, prev = _cross_up()
    rt.on_bar(_bar(0), now, _view(_bar(0), now, prev))
    rt.on_fill(Fill("c", "b", "acct", "AAAA", Side.BUY, 50, 10.0, _bar(1).ts, "f0"))
    assert rt.state is State.IN_POSITION and rt.qty == 50
    rt.on_fill(Fill("c", "b", "acct", "AAAA", Side.BUY, 50, 10.0, _bar(1).ts, "f1"))
    assert rt.qty == 100 and rt.entry_price == 10.0
    # a bar whose low breaks the 3% stop -> exit decision
    nc, pc = _no_cross()
    bar = _bar(2, close=9.5, low=9.0)
    decisions = rt.on_bar(bar, nc, _view(bar, nc, pc))
    assert any(d.kind == "exit" and "stop_loss_pct" in d.reason for d in decisions)
    assert rt.state is State.EXIT_PENDING
    rt.on_fill(Fill("c2", "b", "acct", "AAAA", Side.SELL, 100, 9.0, bar.ts, "f2"))
    assert rt.state is State.FLAT and rt.qty == 0


def test_halted_overlay_blocks_new_entry():
    rt, cands = _runtime(entry_override=EntryOverride("take_every"))
    rt.on_halt(True)
    now, prev = _cross_up()
    decisions = rt.on_bar(_bar(0), now, _view(_bar(0), now, prev))
    assert decisions == []
    assert cands and cands[-1].skip_reason == "halted"
    assert rt.state not in (State.ENTRY_PENDING, State.IN_POSITION, State.EXIT_PENDING)


def test_random_override_is_seeded_and_deterministic():
    def run():
        rt, cands = _runtime(entry_override=EntryOverride("random", rate=0.5, seed=42))
        now, prev = _cross_up()
        nc, pc = _no_cross()
        taken = 0
        for i in range(0, 40, 2):
            # alternate cross / no-cross so a trigger fires every other bar
            if rt.on_bar(_bar(i), now, _view(_bar(i), now, prev)):
                taken += 1
            rt.on_bar(_bar(i + 1), nc, _view(_bar(i + 1), nc, pc))
            # reset to FLAT so each trigger is independent
            rt.reset_entry()
        return taken
    assert run() == run()
