"""DecisionRuntime: entry/skip emission with a fake policy, and an ATR-bracket exit
end-to-end through run_backtest (docs/DECISION_MODEL.md §7, §9)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from sgt.core.interfaces import IndicatorSpec
from sgt.core.types import Bar, Fill, Segment, Side
from sgt.decision.features import CostModelAdapter
from sgt.decision.runtime import DecisionRuntime, PolicyContext, PolicyDecision
from sgt.engine.backtest import run_backtest
from sgt.strategy.base import EvalView, HistoryView
from sgt.strategy.runtime import State
from sgt.strategy.schema import ModelStrategyConfig

_T0 = datetime(2024, 3, 12, 14, 0, tzinfo=timezone.utc)
_SD = date(2024, 3, 12)
_ATR_KEY = f"{IndicatorSpec.of('atr', n=14).key}.value"


def _bar(i, o, h, l, c, v=100000.0) -> Bar:
    return Bar(symbol="AAAA", ts=_T0 + timedelta(minutes=i), open=o, high=h, low=l, close=c,
               volume=v, session_date=_SD, minute_of_day=600 + i, segment=Segment.RTH)


def _cfg(**over) -> ModelStrategyConfig:
    raw = {
        "id": "m1", "universe": {"float_max": 1e12, "price_min": 0.1},
        "triggers": ["hod_break"],
        "bracket": {"k_tp": 1.0, "k_sl": 2.0, "time_stop_minutes": 120},
        "sizing": {"risk_per_trade_pct_of_allocation": 0.5, "allocation_usd": 10_000.0,
                   "max_notional_usd": 20_000.0, "entry_offset_bps": 300.0},
        "model": {"artifact_path": "x", "max_size_fraction": 0.05},
    }
    raw.update(over)
    return ModelStrategyConfig.model_validate(raw)


class _FakePolicy:
    def __init__(self, trade=True, reason="fake", size_fraction=0.02):
        self.trade = trade
        self.reason = reason
        self.size_fraction = size_fraction
        self.seen: list[PolicyContext] = []

    def decide(self, features, context):
        self.seen.append(context)
        return PolicyDecision(
            trade=self.trade, size_fraction=self.size_fraction,
            stop_price=context.close * 0.90, take_profit_price=context.close * 1.1,
            reason=self.reason, contributions={"mu": 0.01, "p": 0.6})


def _view(bar, *, hod=None, atr=0.5):
    snap = {_ATR_KEY: atr}
    return EvalView(bar=bar, snap_now=snap, snap_prev=snap, windows=frozenset({"rth"}),
                    history=HistoryView([], [], [], [], []), position=None, reference=None,
                    session_high_prior=hod, premarket_high=None)


# ------------------------------------------------------------------ standalone FSM

def test_runtime_emits_entry_on_trigger_and_trade():
    cands: list = []
    policy = _FakePolicy(trade=True)
    rt = DecisionRuntime(_cfg(), "AAAA", policy=policy, cost_source=CostModelAdapter(),
                         candidate_sink=cands.append, allow_unknown_float=True)
    bar = _bar(0, 20.0, 20.6, 19.9, 20.5)  # close > prior hod -> hod_break
    decisions = rt.on_bar(bar, _view(bar, hod=20.0).snap_now, _view(bar, hod=20.0))
    assert len(decisions) == 1
    d = decisions[0]
    assert d.kind == "entry" and d.qty_hint >= 1
    assert d.stop_price < d.limit_price
    assert d.features["policy_mu"] == 0.01 and d.features["policy_p"] == 0.6
    assert rt.state is State.ENTRY_PENDING
    assert policy.seen and policy.seen[0].symbol == "AAAA"


def test_runtime_skips_when_policy_declines():
    cands: list = []
    rt = DecisionRuntime(_cfg(), "AAAA", policy=_FakePolicy(trade=False, reason="low_mu"),
                         cost_source=CostModelAdapter(), candidate_sink=cands.append,
                         allow_unknown_float=True)
    bar = _bar(0, 20.0, 20.6, 19.9, 20.5)
    decisions = rt.on_bar(bar, _view(bar, hod=20.0).snap_now, _view(bar, hod=20.0))
    assert decisions == []
    assert rt.state is State.FLAT
    assert len(cands) == 1 and cands[0].taken is False and cands[0].skip_reason == "low_mu"
    assert cands[0].features["policy_mu"] == 0.01


def test_runtime_vetoes_on_price_band():
    cands: list = []
    rt = DecisionRuntime(_cfg(universe={"float_max": 1e12, "price_min": 50.0}), "AAAA",
                         policy=_FakePolicy(trade=True), cost_source=CostModelAdapter(),
                         candidate_sink=cands.append, allow_unknown_float=True)
    bar = _bar(0, 20.0, 20.6, 19.9, 20.5)  # below price_min 50
    decisions = rt.on_bar(bar, _view(bar, hod=20.0).snap_now, _view(bar, hod=20.0))
    assert decisions == []
    assert cands[0].skip_reason == "universe:price"


# ------------------------------------------------------------------ F1: frozen entry ATR

def _enter_and_fill(rt, *, entry_atr, entry_price=20.5):
    """Drive the FSM to IN_POSITION with the signal-bar ATR frozen on the position."""
    bar = _bar(0, 20.0, 20.6, 19.9, 20.5)  # close > prior hod -> hod_break
    rt.on_bar(bar, _view(bar, hod=20.0, atr=entry_atr).snap_now,
              _view(bar, hod=20.0, atr=entry_atr))
    assert rt._entry_atr == entry_atr
    rt.on_fill(Fill(client_order_id="c", broker_order_id="b", account_id="a", symbol="AAAA",
                    side=Side.BUY, qty=10, price=entry_price,
                    ts=_T0 + timedelta(minutes=1), fill_id="f1"))
    assert rt.state is State.IN_POSITION


def _exit_bar(i, h, l, c=20.5, o=20.5):
    return _bar(i, o, h, l, c)


def test_exit_uses_frozen_entry_atr_when_live_atr_expands():
    # Entry ATR 0.5 (frozen); k_tp=1 -> TP barrier = 20.5 + 1*0.5 = 21.0. Live ATR later
    # blows out to 5.0 (barrier would be 25.5 and never hit) but the frozen barrier holds.
    rt = DecisionRuntime(_cfg(bracket={"k_tp": 1.0, "k_sl": 1.0, "time_stop_minutes": 120}),
                         "AAAA", policy=_FakePolicy(trade=True), cost_source=CostModelAdapter(),
                         allow_unknown_float=True)
    _enter_and_fill(rt, entry_atr=0.5, entry_price=20.5)
    bar = _exit_bar(2, h=21.0, l=20.4, c=20.8)  # high hits frozen TP 21.0; low 20.4 > SL 19.5
    view = _view(bar, atr=5.0)          # live ATR expanded
    decisions = rt.on_bar(bar, view.snap_now, view)
    assert len(decisions) == 1 and decisions[0].reason == "exit:atr_take_profit"


def test_same_bar_double_touch_resolves_to_stop():
    # A bar touching BOTH frozen barriers must exit at the STOP (labels resolve a same-bar
    # double touch as a stop; DECISION_MODEL.md §5 F1). Frozen ATR 0.5, entry 20.5:
    # TP=21.0, SL=20.0 (k_sl=1). Bar high 21.5 >= TP and low 19.5 <= SL -> both touched.
    rt = DecisionRuntime(_cfg(bracket={"k_tp": 1.0, "k_sl": 1.0, "time_stop_minutes": 120}),
                         "AAAA", policy=_FakePolicy(trade=True), cost_source=CostModelAdapter(),
                         allow_unknown_float=True)
    _enter_and_fill(rt, entry_atr=0.5, entry_price=20.5)
    bar = _exit_bar(2, h=21.5, l=19.5)
    view = _view(bar, atr=5.0)
    decisions = rt.on_bar(bar, view.snap_now, view)
    assert len(decisions) == 1 and decisions[0].reason == "exit:atr_stop"


# ------------------------------------------------------------------ F4: exit/label consistency

class _StubConsistencyPolicy:
    """Exposes the real policy's F4 surface (``params`` + ``model_metadata``)."""

    def __init__(self, label_col, k_tp, k_sl):
        from sgt.decision.policy import PolicyParams
        self.params = PolicyParams(k_tp=k_tp, k_sl=k_sl, n_eff=100)
        self.model_metadata = {"label_col": label_col}

    def decide(self, features, context):  # pragma: no cover - never reached in these tests
        return PolicyDecision(False, 0.0, None, None, "stub")


def test_runtime_rejects_bracket_label_mismatch():
    cfg = _cfg(bracket={"k_tp": 1.0, "k_sl": 2.0, "time_stop_minutes": 20})  # k_tp 1.0
    pol = _StubConsistencyPolicy("y_net__tp2.0_sl2.0_h20", k_tp=2.0, k_sl=2.0)  # label tp 2.0
    with pytest.raises(ValueError, match="F4"):
        DecisionRuntime(cfg, "AAAA", policy=pol, allow_unknown_float=True)


def test_runtime_rejects_time_stop_mismatch():
    cfg = _cfg(bracket={"k_tp": 2.0, "k_sl": 2.0, "time_stop_minutes": 30})  # 30 != H=20
    pol = _StubConsistencyPolicy("y_net__tp2.0_sl2.0_h20", k_tp=2.0, k_sl=2.0)
    with pytest.raises(ValueError, match="time-stop"):
        DecisionRuntime(cfg, "AAAA", policy=pol, allow_unknown_float=True)


def test_runtime_accepts_consistent_bracket():
    cfg = _cfg(bracket={"k_tp": 2.0, "k_sl": 2.0, "time_stop_minutes": 20})
    pol = _StubConsistencyPolicy("y_net__tp2.0_sl2.0_h20", k_tp=2.0, k_sl=2.0)
    rt = DecisionRuntime(cfg, "AAAA", policy=pol, allow_unknown_float=True)
    assert rt.state is State.FLAT


# ------------------------------------------------------------------ end-to-end via run_backtest

def _trend_series() -> list[Bar]:
    bars = [_bar(i, 20.0, 20.3, 19.7, 20.0) for i in range(15)]  # warm ATR
    bars.append(_bar(15, 20.0, 20.6, 19.9, 20.5))  # breakout -> hod_break
    c = 20.5
    for i in range(16, 40):
        c += 0.2
        bars.append(_bar(i, c - 0.2, c + 0.2, c - 0.3, c))
    return bars


def test_backtest_model_strategy_exits_via_atr_take_profit():
    policy = _FakePolicy(trade=True, size_fraction=0.02)
    res = run_backtest(
        _cfg(), {"AAAA": _trend_series()}, starting_cash=100_000.0,
        policy=policy, cost_source=CostModelAdapter(), allow_unknown_float=True)
    assert len(res.trades) == 1
    trade = res.trades.iloc[0]
    assert trade["exit_reason"] == "atr_take_profit"
    assert trade["qty"] >= 1 and trade["pnl"] > 0
    # A taken candidate is journaled with the policy diagnostics.
    taken = res.candidates[res.candidates["taken"]]
    assert len(taken) == 1
    assert taken.iloc[0]["features"]["policy_p"] == 0.6


def test_backtest_model_no_trade_when_policy_declines():
    res = run_backtest(
        _cfg(), {"AAAA": _trend_series()}, starting_cash=100_000.0,
        policy=_FakePolicy(trade=False, reason="hurdle"), cost_source=CostModelAdapter(),
        allow_unknown_float=True)
    assert len(res.trades) == 0
    assert (res.candidates["skip_reason"] == "hurdle").any()


# ------------------------------------------------------------------ portfolio at-risk cap

class _CapPolicy:
    """Fake with the real policy's contract (``sgt.decision.policy``): it blocks a new
    position once ``context.open_risk`` (the engine's strategy-wide at-risk fraction) has
    reached the portfolio cap. This is the money-path wiring the test exercises."""

    def __init__(self, request=0.05, cap=0.04):
        self.request = request
        self.cap = cap

    def decide(self, features, context):
        stop = context.mark_price * 0.90  # 20 -> 18, so (entry - stop) = 2 exactly
        contrib = [("open_risk", context.open_risk)]
        if context.open_risk >= self.cap:
            return PolicyDecision(False, 0.0, stop, None, "portfolio_cap", contrib)
        return PolicyDecision(True, self.request, stop, context.mark_price * 1.5, "trade", contrib)


def _cap_bars(symbol, breakout_i):
    base = datetime(2024, 3, 12, 14, 0, tzinfo=timezone.utc)

    def b(i, o, h, l, c):
        return Bar(symbol=symbol, ts=base + timedelta(minutes=i), open=o, high=h, low=l, close=c,
                   volume=100000.0, session_date=_SD, minute_of_day=600 + i, segment=Segment.RTH)

    bars = [b(i, 19.0, 19.3, 18.7, 19.0) for i in range(breakout_i)]  # warm ATR, session high ~19.3
    bars.append(b(breakout_i, 19.0, 20.1, 18.9, 20.0))               # breakout -> hod_break, close 20
    bars.append(b(breakout_i + 1, 20.0, 20.4, 19.5, 20.0))           # entry fills at limit 20
    for i in range(breakout_i + 2, 42):                              # hold flat: no TP (<20.6), no stop
        bars.append(b(i, 20.0, 20.4, 19.6, 20.0))
    return bars


def test_portfolio_at_risk_cap_blocks_second_symbol():
    # entry_offset 0 so the limit == close == 20 and the fill is exactly 20; (20-18)*qty
    # gives AAAA 0.05 of allocation at risk, which saturates the 0.04 cap for BBBB.
    cfg = _cfg(sizing={"risk_per_trade_pct_of_allocation": 0.5, "allocation_usd": 10_000.0,
                       "max_notional_usd": 20_000.0, "entry_offset_bps": 0.0})
    policy = _CapPolicy(request=0.05, cap=0.04)
    # AAAA breaks out first (bar 15, fills bar 16); BBBB breaks out later (bar 20) once
    # AAAA's at-risk already crosses the cap.
    bars = {"AAAA": _cap_bars("AAAA", 15), "BBBB": _cap_bars("BBBB", 20)}
    res = run_backtest(cfg, bars, starting_cash=1_000_000.0, policy=policy,
                       cost_source=CostModelAdapter(), allow_unknown_float=True)

    taken = res.candidates[res.candidates["taken"]]
    assert set(taken["symbol"]) == {"AAAA"}  # only AAAA opened
    # BBBB was blocked by the portfolio cap: its open_risk reflects AAAA's 0.05 at-risk,
    # aggregated across the engine's runtimes (not just BBBB's own book).
    bbbb = res.candidates[(res.candidates["symbol"] == "BBBB")
                          & (res.candidates["skip_reason"] == "portfolio_cap")]
    assert len(bbbb) >= 1
    assert bbbb.iloc[0]["features"]["policy_open_risk"] == pytest.approx(0.05)
