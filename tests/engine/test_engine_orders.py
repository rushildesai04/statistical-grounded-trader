"""OrderLifecycle (F-5) regression tests: protective-stop placement/restoration,
exit latching + retry, entry timeout, session-end forced exit, the open-position
invariant, plus backtest-level checks that an illiquid session cannot strand a
position without protection or leak a false ``end_of_data``."""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

import pytest

from sgt.core.types import Bar, Fill, OrderAck, OrderStatus, OrderType, Segment, Side
from sgt.engine.backtest import run_backtest
from sgt.engine.orders import BrokerCaps, CancelAction, OrderLifecycle, SubmitAction
from sgt.strategy.base import Decision
from sgt.strategy.runtime import State, StrategyRuntime
from sgt.strategy.schema import StrategyConfig

_ET = ZoneInfo("America/New_York")


def _cfg(ext: bool = False) -> StrategyConfig:
    return StrategyConfig.model_validate({
        "id": "t", "universe": {"float_max": 1e9, "price_min": 0.1},
        "trigger": {"kind": "ema_cross_up", "fast": 9, "slow": 24},
        "confirm": {"k_of_m": 1, "signals": [{"kind": "pvt_state", "state": "above_signal"}]},
        "exits": {"any_of": [
            {"kind": "stop_loss_pct", "pct": 3.0},
            {"kind": "time_stop_minutes", "n": 30},
            {"kind": "session_end", "minutes_before_close": 5, "window": "post" if ext else "rth"}]},
        "sizing": {"risk_per_trade_pct_of_allocation": 0.5,
                   "allocation_usd": 10_000.0, "max_notional_usd": 20_000.0},
        "extended_hours": ext,
    })


def _bar(minute=600, seg=Segment.RTH, vol=1000.0, close=10.0, d=date(2024, 3, 12)) -> Bar:
    hh, mm = divmod(minute, 60)
    ts = datetime.combine(d, time(hh, mm), _ET).astimezone(timezone.utc)
    return Bar("AAAA", ts, close, close + 0.1, close - 0.1, close, vol, d, minute, seg)


def _lifecycle(cfg, caps=BrokerCaps(True, True)):
    rts: dict[str, StrategyRuntime] = {}
    rts["AAAA"] = StrategyRuntime(cfg, "AAAA", allow_unknown_float=True)
    lc = OrderLifecycle(cfg, account_id="a", strategy_id=cfg.id, config_hash="h",
                        caps=caps, runtime_for=rts.get, entry_timeout_bars=2)
    return lc, rts


def _entry(bar) -> Decision:
    return Decision(kind="entry", symbol="AAAA", reason="entry", signal_ts=bar.ts,
                    limit_price=10.01, stop_price=9.71, qty_hint=100, features={})


def _submits(actions):
    return [a for a in actions if isinstance(a, SubmitAction)]


def test_entry_timeout_cancels_and_resets_runtime():
    lc, rts = _lifecycle(_cfg())
    rts["AAAA"].state = State.ENTRY_PENDING
    lc.on_decision(_entry(_bar(600)), _bar(600))
    cancels = []
    for i in range(1, 5):
        cancels += [a for a in lc.on_bar_tick(_bar(600 + i)) if isinstance(a, CancelAction)]
    assert cancels, "unfilled entry must be cancelled after the timeout"
    assert rts["AAAA"].state is State.FLAT


def test_protective_stop_placed_on_entry_fill():
    lc, _ = _lifecycle(_cfg())
    b = _bar(600)
    entry_cid = _submits(lc.on_decision(_entry(b), b))[0].intent.client_order_id
    acts = lc.on_fill(Fill(entry_cid, "x", "a", "AAAA", Side.BUY, 100, 10.0, b.ts, "f0"), _bar(601))
    stops = [a for a in _submits(acts) if a.intent.order_type is OrderType.STOP]
    assert stops and stops[0].intent.qty == 100 and stops[0].intent.stop_price == 9.71


def test_exit_retried_when_terminal_unfilled():
    lc, _ = _lifecycle(_cfg())
    b = _bar(600)
    entry_cid = _submits(lc.on_decision(_entry(b), b))[0].intent.client_order_id
    lc.on_fill(Fill(entry_cid, "x", "a", "AAAA", Side.BUY, 100, 10.0, b.ts, "f0"), _bar(601))
    ex = Decision(kind="exit", symbol="AAAA", reason="exit:macd_hist_state",
                  signal_ts=_bar(602).ts, limit_price=9.9, qty_hint=100)
    acts = lc.on_decision(ex, _bar(602))
    assert any(isinstance(a, CancelAction) for a in acts), "stop cancelled before discretionary exit"
    exit_cid = _submits(acts)[0].intent.client_order_id
    # the exit order expires unfilled while still holding -> a fresh exit must be issued
    ack = OrderAck(exit_cid, "x", OrderStatus.EXPIRED, _bar(603).ts)
    retry = _submits(lc.on_order_terminal(ack, _bar(603)))
    assert retry and retry[0].intent.side is Side.SELL
    assert retry[0].intent.client_order_id != exit_cid


def test_stop_restored_when_it_terminates_while_holding():
    lc, _ = _lifecycle(_cfg())
    b = _bar(600)
    entry_cid = _submits(lc.on_decision(_entry(b), b))[0].intent.client_order_id
    stop_cid = _submits(lc.on_fill(
        Fill(entry_cid, "x", "a", "AAAA", Side.BUY, 100, 10.0, b.ts, "f0"), _bar(601)))[0].intent.client_order_id
    ack = OrderAck(stop_cid, "x", OrderStatus.CANCELED, _bar(602).ts)
    restored = [a for a in _submits(lc.on_order_terminal(ack, _bar(602)))
                if a.intent.order_type is OrderType.STOP]
    assert restored, "a stop that terminates while holding must be re-placed"


def test_session_roll_forces_flatten_exit():
    lc, rts = _lifecycle(_cfg(ext=True))
    b = _bar(600)
    entry_cid = _submits(lc.on_decision(_entry(b), b))[0].intent.client_order_id
    fill = Fill(entry_cid, "x", "a", "AAAA", Side.BUY, 100, 10.0, b.ts, "f0")
    rts["AAAA"].on_fill(fill)          # the executor routes fills to the runtime (engine.on_fill)
    lc.on_fill(fill, _bar(601))
    assert rts["AAAA"].state is State.IN_POSITION
    nxt = _bar(600, d=date(2024, 3, 13))
    subs = _submits(lc.on_session_roll("AAAA", nxt))
    assert subs and subs[0].intent.reason == "session_end_forced"
    assert rts["AAAA"].state is State.EXIT_PENDING


def test_invariant_detects_unprotected_rth_position():
    lc, _ = _lifecycle(_cfg())
    b = _bar(600)
    entry_cid = _submits(lc.on_decision(_entry(b), b))[0].intent.client_order_id
    lc.on_fill(Fill(entry_cid, "x", "a", "AAAA", Side.BUY, 100, 10.0, b.ts, "f0"), _bar(601))
    assert lc.check_invariant(_bar(602), halted=False) == []  # stop working -> sound
    # artificially break protection: stop gone, no exit -> violation in RTH
    lc._state["AAAA"].stop.terminal = True
    assert lc.check_invariant(_bar(603), halted=False)
    # extended hours is an accepted no-broker-stop window -> no violation
    assert lc.check_invariant(_bar(1100, seg=Segment.POST), halted=False) == []


# --------------------------------------------------------------------------- backtest-level F-5


def _ramp(decline_n, rise_n, rise_step=0.25):
    closes = [10.0 - 0.03 * i for i in range(decline_n)]
    last = closes[-1]
    return closes + [last + rise_step * i for i in range(1, rise_n + 1)]


def _mk_bars(d, start_minute, closes, vol, seg):
    bars = []
    for i, c in enumerate(closes):
        m = start_minute + i
        hh, mm = divmod(m, 60)
        ts = datetime.combine(d, time(hh, mm), _ET).astimezone(timezone.utc)
        bars.append(Bar("AAAA", ts, c, c + 0.1, c - 0.3, c, float(vol), d, m, seg))
    return bars


_ILLIQUID_CFG = StrategyConfig.model_validate({
    "id": "ill", "universe": {"float_max": 1e9, "price_min": 0.1},
    "regime": [{"kind": "session_window_in", "windows": ["rth", "after_hours"]}],
    "trigger": {"kind": "ema_cross_up", "fast": 9, "slow": 24},
    "confirm": {"k_of_m": 1, "signals": [{"kind": "pvt_state", "state": "above_signal"}]},
    "exits": {"any_of": [
        {"kind": "stop_loss_pct", "pct": 3.0},
        {"kind": "time_stop_minutes", "n": 30},
        {"kind": "session_end", "minutes_before_close": 5, "window": "post"}]},
    "sizing": {"risk_per_trade_pct_of_allocation": 0.5,
               "allocation_usd": 10_000.0, "max_notional_usd": 20_000.0},
    "extended_hours": True,
})


def test_illiquid_session_strands_nothing_and_holds_invariant():
    from sgt.intraday.calendar import default_calendar
    from sgt.strategy.runtime import EntryOverride

    s1, s2 = default_calendar().previous_sessions(date(2024, 3, 15), 2)
    bars = []
    bars += _mk_bars(s1, 575, _ramp(28, 10), 50_000, Segment.RTH)   # RTH: decline->rise entry
    bars += _mk_bars(s1, 960, [12.5] * 30, 0, Segment.POST)          # POST: ZERO volume (illiquid)
    bars += _mk_bars(s2, 575, [12.5 + 0.02 * i for i in range(30)], 50_000, Segment.RTH)  # liquid next day
    res = run_backtest(_ILLIQUID_CFG, {"AAAA": bars}, starting_cash=100_000.0,
                       entry_override=EntryOverride("take_every"), run_id="ill", allow_unknown_float=True)
    violations = [e for e in res.events.events if e["kind"] == "invariant_violation"]
    assert not violations, f"open-position invariant breached: {violations}"
    assert res.summary["n_trades"] >= 1
    reasons = set(res.trades["exit_reason"])
    # the position carried across the illiquid boundary must be force-exited, not stranded
    assert reasons & {"session_end_forced", "carried_overnight"}
    # no end_of_data stranding: the only allowed end_of_data is a genuinely-open final bar
    stranded = res.trades[(res.trades["exit_reason"] == "end_of_data") &
                          (res.trades["exit_ts"] < bars[-1].ts)]
    assert stranded.empty


def test_no_entries_within_final_minutes_of_close():
    from sgt.strategy.runtime import EntryOverride

    cfg = StrategyConfig.model_validate({
        "id": "rthonly", "universe": {"float_max": 1e9, "price_min": 0.1},
        "trigger": {"kind": "ema_cross_up", "fast": 9, "slow": 24},
        "confirm": {"k_of_m": 1, "signals": [{"kind": "pvt_state", "state": "above_signal"}]},
        "exits": {"any_of": [
            {"kind": "stop_loss_pct", "pct": 3.0},
            {"kind": "time_stop_minutes", "n": 30},
            {"kind": "session_end", "minutes_before_close": 10, "window": "rth"}]},
        "sizing": {"risk_per_trade_pct_of_allocation": 0.5,
                   "allocation_usd": 10_000.0, "max_notional_usd": 20_000.0},
        "extended_hours": False,
    })
    d = date(2024, 3, 12)
    # a cross right at the final 10 RTH minutes (rth close 16:00 ET = minute 960)
    closes = _ramp(28, 8)
    bars = _mk_bars(d, 924, closes, 50_000, Segment.RTH)  # 924..959 -> last bars within 10m of close
    res = run_backtest(cfg, {"AAAA": bars}, starting_cash=100_000.0,
                       entry_override=EntryOverride("take_every"), run_id="cutoff", allow_unknown_float=True)
    cand = res.candidates
    late = cand[cand["ts"].apply(lambda t: t.astimezone(_ET).hour * 60 + t.astimezone(_ET).minute >= 950)]
    assert not late.empty
    assert (late["skip_reason"] == "session_cutoff").all()
    assert not late["taken"].any()


# --------------------------------------------------------------------------- E-1: stop sizing

def _entry_qty(bar, qty, limit=10.5, stop=9.71) -> Decision:
    return Decision(kind="entry", symbol="AAAA", reason="entry", signal_ts=bar.ts,
                    limit_price=limit, stop_price=stop, qty_hint=qty, features={})


def test_partial_entry_resizes_stop_to_full_position_direct():
    """Two partial BUY fills (3 then 2) on a 5-share entry must leave ONE working stop
    sized to the full 5-share position, not the first partial (E-1)."""
    lc, _ = _lifecycle(_cfg())
    b = _bar(600)
    cid = _submits(lc.on_decision(_entry_qty(b, 5), b))[0].intent.client_order_id
    a1 = lc.on_fill(Fill(cid, "x", "a", "AAAA", Side.BUY, 3, 10.0, b.ts, "f0"), _bar(601))
    stops1 = [a for a in _submits(a1) if a.intent.order_type is OrderType.STOP]
    assert stops1 and stops1[0].intent.qty == 3
    a2 = lc.on_fill(Fill(cid, "x", "a", "AAAA", Side.BUY, 2, 10.0, b.ts, "f1"), _bar(602))
    assert [a for a in a2 if isinstance(a, CancelAction) and a.reason == "resize_stop"]
    stops2 = [a for a in _submits(a2) if a.intent.order_type is OrderType.STOP]
    assert stops2 and stops2[0].intent.qty == 5
    assert lc._state["AAAA"].stop.qty == 5 and lc.position_qty("AAAA") == 5


def test_partial_entry_resizes_stop_via_simbroker_participation_cap():
    """End-to-end through SimBroker whose participation cap (3/bar) forces a 5-share
    entry to fill in two partials; the broker must end with exactly one working SELL
    STOP for the full 5 shares."""
    from sgt.brokers.costs import CostModel
    from sgt.brokers.sim import SimBroker

    sim = SimBroker("a", CostModel(participation_cap=0.01), 100_000.0)
    lc, _ = _lifecycle(_cfg())

    def run(actions, bar):
        for a in actions:
            if isinstance(a, CancelAction):
                sim.cancel_sync(a.client_order_id)
            else:
                sim.submit_sync(a.intent)

    run(lc.on_decision(_entry_qty(_bar(600), 5), _bar(600)), _bar(600))
    for m in (601, 602, 603):
        bar = _bar(m, vol=300.0)
        for fill in sim.on_bar(bar):
            run(lc.on_fill(fill, bar), bar)
        for cid in lc.open_order_ids():
            stt = sim.order_state_sync(cid)
            if stt is not None and stt.status.terminal:
                run(lc.on_order_terminal(
                    OrderAck(cid, stt.broker_order_id, stt.status, bar.ts, symbol=stt.symbol,
                             side=stt.side, remaining_qty=stt.remaining_qty), bar), bar)
    working_stops = [o for o in sim._orders.values()
                     if not o.status.terminal and o.intent.order_type is OrderType.STOP]
    assert len(working_stops) == 1 and working_stops[0].intent.qty == 5
    assert lc.position_qty("AAAA") == 5


def test_second_concurrent_entry_is_rejected():
    """A second entry while the first entry order is still working must not place a
    second order, and must roll the runtime back to FLAT (not freeze it)."""
    lc, rts = _lifecycle(_cfg())
    b = _bar(600)
    assert len(_submits(lc.on_decision(_entry(b), b))) == 1
    rts["AAAA"].state = State.ENTRY_PENDING
    acts = lc.on_decision(_entry(_bar(601)), _bar(601))
    assert _submits(acts) == []
    assert rts["AAAA"].state is State.FLAT


def test_invariant_flags_stop_not_covering_position():
    lc, _ = _lifecycle(_cfg())
    b = _bar(600)
    cid = _submits(lc.on_decision(_entry(b), b))[0].intent.client_order_id
    lc.on_fill(Fill(cid, "x", "a", "AAAA", Side.BUY, 100, 10.0, b.ts, "f0"), _bar(601))
    assert lc.check_invariant(_bar(602), halted=False) == []
    lc._state["AAAA"].stop.qty = 60            # undersized
    msgs = lc.check_invariant(_bar(603), halted=False)
    assert msgs and "below" in msgs[0]
    lc._state["AAAA"].stop.qty = 140           # oversized
    assert lc.check_invariant(_bar(604), halted=False)


# --------------------------------------------------------------------------- L-A: marketable exits

def test_protective_exit_is_marketable_in_extended_hours():
    lc, rts = _lifecycle(_cfg(ext=True))
    b = _bar(600)
    cid = _submits(lc.on_decision(_entry(b), b))[0].intent.client_order_id
    f = Fill(cid, "x", "a", "AAAA", Side.BUY, 100, 10.0, b.ts, "f0")
    rts["AAAA"].on_fill(f)
    lc.on_fill(f, _bar(601))
    post = _bar(1100, seg=Segment.POST, close=10.0)
    subs = _submits(lc.flatten("AAAA", post, "session_end_forced"))
    assert subs and subs[0].intent.order_type is OrderType.LIMIT
    assert subs[0].intent.limit_price == round(10.0 * 0.97, 6)
    # In RTH (broker-side stop available) the protective exit is NOT marketable: it rests
    # at the configured exit offset (0 bps here), with the broker stop backing it.
    lc2, r2 = _lifecycle(_cfg(ext=True))
    cid2 = _submits(lc2.on_decision(_entry(_bar(600)), _bar(600)))[0].intent.client_order_id
    f2 = Fill(cid2, "x", "a", "AAAA", Side.BUY, 100, 10.0, _bar(600).ts, "f0")
    r2["AAAA"].on_fill(f2)
    lc2.on_fill(f2, _bar(601))
    rth = _bar(602, seg=Segment.RTH, close=10.0)
    s2 = _submits(lc2.flatten("AAAA", rth, "session_end_forced"))
    assert s2 and s2[0].intent.limit_price == 10.0        # not marketable in RTH
    assert s2[0].intent.limit_price != round(10.0 * 0.97, 6)


def test_protective_exit_repriced_each_extended_hours_bar():
    lc, rts = _lifecycle(_cfg(ext=True))
    b = _bar(600)
    cid = _submits(lc.on_decision(_entry(b), b))[0].intent.client_order_id
    f = Fill(cid, "x", "a", "AAAA", Side.BUY, 100, 10.0, b.ts, "f0")
    rts["AAAA"].on_fill(f)
    lc.on_fill(f, _bar(601))
    first = _submits(lc.flatten("AAAA", _bar(1100, seg=Segment.POST, close=10.0), "session_end_forced"))[0]
    assert first.intent.limit_price == round(10.0 * 0.97, 6)
    acts = lc.on_bar_tick(_bar(1101, seg=Segment.POST, close=8.0))
    assert any(isinstance(a, CancelAction) and a.reason == "reprice_protective" for a in acts)
    assert _submits(acts)[0].intent.limit_price == round(8.0 * 0.97, 6)
    # Back in RTH there is no re-pricing churn.
    assert lc.on_bar_tick(_bar(1102, seg=Segment.RTH, close=8.0)) == []


def test_simbroker_marketable_sell_fills_on_gap_down_but_thin_offset_does_not():
    from sgt.brokers.costs import DEFAULT_COST_MODEL
    from sgt.brokers.sim import SimBroker, _Lot
    from sgt.core.types import OrderIntent, TimeInForce

    def held():
        s = SimBroker("a", DEFAULT_COST_MODEL, 100_000.0)
        s._positions["AAAA"] = _Lot(qty=100, avg_price=10.0)
        s._bar_seq["AAAA"] = 1   # orders submitted now are eligible on the next on_bar
        return s

    gap = _bar(1100, seg=Segment.POST, close=9.0)  # prior close ~10; this bar trades ~9
    gap = Bar("AAAA", gap.ts, 9.0, 9.05, 8.9, 9.0, 80_000.0, gap.session_date, 1100, Segment.POST)

    def sell(cid, price):
        return OrderIntent(client_order_id=cid, account_id="a", symbol="AAAA", side=Side.SELL,
                           qty=100, order_type=OrderType.LIMIT, limit_price=price, tif=TimeInForce.DAY,
                           extended_hours=True, strategy_id="t", reason="exit:session_end_forced",
                           created_at=_bar(1099, seg=Segment.POST).ts, stop_price=None)

    thin = held()
    thin.submit_sync(sell("thin-1", round(10.0 * (1 - 10 * 1e-4), 6)))  # 10 bps below the OLD close
    assert thin.on_bar(gap) == []                                       # rests above the gapped market

    mk = held()
    mk.submit_sync(sell("mk-1", round(9.0 * 0.97, 6)))                  # 3% below the gapped close
    fills = mk.on_bar(gap)
    assert fills and fills[0].price >= round(9.0 * 0.97, 6)
    assert mk._positions["AAAA"].qty == 0
