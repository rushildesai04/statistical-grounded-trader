"""Condition evaluation on hand-built snapshots, incl. None -> False and the
MACD 4-state mapping, divergence, exhaustion and HOD break."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from sgt.core.interfaces import ReferenceData
from sgt.core.types import Bar, Segment
from sgt.strategy.base import EvalView, HistoryView, PositionView
from sgt.strategy.conditions import (
    EmaCrossUp,
    HodBreak,
    MacdHistState,
    NoDilution,
    PsarFlip,
    PsarState,
    PvtBullishDivergence,
    RsiLt,
    SessionWindowIn,
    VolumeExhaustion,
)
from sgt.strategy.exits import AtrStop, AtrTakeProfit, StopLossPct, TimeStopMinutes

_ATR_KEY = "atr(n=14).value"


def _bar(close=10.0, high=None, low=None, vol=1000.0, minute=600, seg=Segment.RTH) -> Bar:
    high = high if high is not None else close + 0.5
    low = low if low is not None else close - 0.5
    return Bar(symbol="AAAA", ts=datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc),
               open=close, high=high, low=low, close=close, volume=vol,
               session_date=date(2024, 3, 12), minute_of_day=minute, segment=seg)


def _view(bar, now, prev=None, *, history=None, position=None, reference=None,
          windows=frozenset(), session_high=None):
    h = history or HistoryView([], [], [], [], [])
    return EvalView(bar=bar, snap_now=now, snap_prev=prev, windows=windows, history=h,
                    position=position, reference=reference, session_high_prior=session_high)


def test_ema_cross_up_fires_only_on_the_cross():
    fk, sk = "ema(n=9).value", "ema(n=24).value"
    c = EmaCrossUp(fast=9, slow=24)
    now = {fk: 10.1, sk: 10.0}
    prev = {fk: 9.9, sk: 10.0}
    assert c.evaluate(_view(_bar(), now, prev)) is True
    # already above on prev -> not a fresh cross
    assert c.evaluate(_view(_bar(), now, {fk: 10.05, sk: 10.0})) is False


def test_missing_or_none_inputs_are_false_never_raise():
    c = EmaCrossUp(fast=9, slow=24)
    assert c.evaluate(_view(_bar(), {"ema(n=9).value": None, "ema(n=24).value": 10.0},
                            {"ema(n=9).value": 9.0, "ema(n=24).value": 10.0})) is False
    assert c.evaluate(_view(_bar(), {}, None)) is False  # no prev snapshot, no keys


@pytest.mark.parametrize("h,p,state,expected", [
    (1.0, 0.5, "Up1", True), (1.0, 1.5, "Up1", False),
    (1.0, 1.0, "Up2", True), (1.0, 0.5, "Up2", False),
    (-1.0, -0.5, "Down1", True), (-1.0, -1.5, "Down1", False),
    (-1.0, -1.0, "Down2", True), (-1.0, -0.5, "Down2", False),
])
def test_macd_hist_four_state(h, p, state, expected):
    key = "macd(fast=12,signal=9,slow=26).hist"
    c = MacdHistState(state=state)
    assert c.evaluate(_view(_bar(), {key: h}, {key: p})) is expected


def test_psar_state_and_flip():
    key = "psar().trend"
    assert PsarState(state="below_price").evaluate(_view(_bar(), {key: 1.0})) is True
    assert PsarState(state="below_price").evaluate(_view(_bar(), {key: -1.0})) is False
    assert PsarFlip(direction="down").evaluate(_view(_bar(), {key: -1.0}, {key: 1.0})) is True
    assert PsarFlip(direction="down").evaluate(_view(_bar(), {key: -1.0}, {key: -1.0})) is False


def test_rsi_lt_and_session_window():
    assert RsiLt(n=14, value=70.0).evaluate(_view(_bar(), {"rsi(n=14).value": 55.0})) is True
    assert RsiLt(n=14, value=70.0).evaluate(_view(_bar(), {"rsi(n=14).value": 80.0})) is False
    v = _view(_bar(), {}, windows=frozenset({"power_hour", "rth"}))
    assert SessionWindowIn(windows=frozenset({"power_hour"})).evaluate(v) is True
    assert SessionWindowIn(windows=frozenset({"pre"})).evaluate(v) is False


def test_no_dilution_fails_closed_when_reference_unknown():
    assert NoDilution().evaluate(_view(_bar(), {})) is False
    ref_ok = ReferenceData(symbol="AAAA", as_of=date(2024, 3, 12), dilution_flag=False)
    ref_bad = ReferenceData(symbol="AAAA", as_of=date(2024, 3, 12), dilution_flag=True)
    assert NoDilution().evaluate(_view(_bar(), {}, reference=ref_ok)) is True
    assert NoDilution().evaluate(_view(_bar(), {}, reference=ref_bad)) is False


def test_hod_break():
    c = HodBreak()
    assert c.evaluate(_view(_bar(close=11.0), {}, session_high=10.5)) is True
    assert c.evaluate(_view(_bar(close=10.0), {}, session_high=10.5)) is False
    assert c.evaluate(_view(_bar(close=11.0), {}, session_high=None)) is False


def test_pvt_bullish_divergence():
    key = "pvt().pvt"
    # price makes a lower low (9.0 < prior min 9.5) while PVT holds above its low.
    hist = HistoryView(closes=[10, 10, 10], highs=[11, 11, 11], lows=[9.8, 9.6, 9.5],
                       volumes=[1, 1, 1], snaps=[{key: 100.0}, {key: 90.0}, {key: 95.0}])
    bar = _bar(close=9.2, low=9.0)
    c = PvtBullishDivergence(lookback=4)
    assert c.evaluate(_view(bar, {key: 98.0}, history=hist)) is True
    # PVT also makes a lower low -> no divergence
    assert c.evaluate(_view(bar, {key: 80.0}, history=hist)) is False
    # insufficient history -> False
    short = HistoryView([10], [11], [9.5], [1], [{key: 90.0}])
    assert c.evaluate(_view(bar, {key: 98.0}, history=short)) is False


def test_volume_exhaustion():
    hist = HistoryView(closes=[9.0, 9.5, 9.8], highs=[0, 0, 0], lows=[0, 0, 0],
                       volumes=[300.0, 200.0, 150.0], snaps=[{}, {}, {}])
    bar = _bar(close=10.0, vol=100.0)
    c = VolumeExhaustion(price_rising_bars=3, volume_falling_bars=3)
    assert c.evaluate(_view(bar, {}, history=hist)) is True
    # a volume uptick breaks the falling-volume requirement
    bar2 = _bar(close=10.0, vol=400.0)
    assert c.evaluate(_view(bar2, {}, history=hist)) is False


def test_exit_conditions_need_a_position():
    bar = _bar(close=9.0, low=8.0)
    pos = PositionView(qty=100, entry_price=10.0, entry_ts=bar.ts, high_since_entry=10.0, bars_held=5)
    # low 8.0 <= 10 * (1 - 0.03) = 9.7 -> stop hit
    assert StopLossPct(pct=3.0).evaluate(_view(bar, {}, position=pos)) is True
    assert StopLossPct(pct=3.0).evaluate(_view(bar, {}, position=None)) is False
    later = _bar(close=10.0)
    later = Bar(symbol="AAAA", ts=datetime(2024, 3, 12, 15, 40, tzinfo=timezone.utc),
                open=10, high=10.5, low=9.5, close=10, volume=1000,
                session_date=date(2024, 3, 12), minute_of_day=640, segment=Segment.RTH)
    pos2 = PositionView(qty=100, entry_price=10.0,
                        entry_ts=datetime(2024, 3, 12, 15, 0, tzinfo=timezone.utc),
                        high_since_entry=10.5, bars_held=40)
    assert TimeStopMinutes(n=30).evaluate(_view(later, {}, position=pos2)) is True
    assert TimeStopMinutes(n=60).evaluate(_view(later, {}, position=pos2)) is False


def test_atr_brackets_use_frozen_entry_atr_not_live():
    # ATR has expanded to 2.0 live, but the barriers must stay at the entry ATR (0.5),
    # matching the training label's FIXED signal-bar barriers (DECISION_MODEL.md §5 F1).
    bar = _bar(close=10.5, high=10.55, low=10.45)  # moved only +/-0.05 from entry 10.0
    snap = {_ATR_KEY: 2.0}  # live ATR blown out
    frozen = PositionView(qty=100, entry_price=10.0, entry_ts=bar.ts, high_since_entry=10.5,
                          bars_held=3, entry_atr=0.5)
    # k_tp=1 -> barrier 10.0 + 1*0.5 = 10.5; bar.high 10.55 >= 10.5 -> hit with frozen ATR.
    assert AtrTakeProfit(k=1.0).evaluate(_view(bar, snap, position=frozen)) is True
    # With the LIVE ATR (2.0) the barrier would be 12.0 and NOT hit; this is the bug fix.
    live = PositionView(qty=100, entry_price=10.0, entry_ts=bar.ts, high_since_entry=10.5,
                        bars_held=3, entry_atr=None)  # rule strategies: fall back to live
    assert AtrTakeProfit(k=1.0).evaluate(_view(bar, snap, position=live)) is False
    # Stop: k_sl=1 -> barrier 10.0 - 0.5 = 9.5; bar.low 10.45 > 9.5 -> not hit (frozen).
    assert AtrStop(k=1.0).evaluate(_view(bar, snap, position=frozen)) is False


def test_atr_brackets_fall_back_to_live_atr_for_rule_strategies():
    # entry_atr None => old behavior: read the live point-in-time ATR from the snapshot.
    bar = _bar(close=11.0, high=12.1, low=10.9)
    snap = {_ATR_KEY: 2.0}
    pos = PositionView(qty=100, entry_price=10.0, entry_ts=bar.ts, high_since_entry=11.0,
                       bars_held=1, entry_atr=None)
    # barrier 10.0 + 1*2.0 = 12.0; high 12.1 >= 12.0 -> hit via live ATR.
    assert AtrTakeProfit(k=1.0).evaluate(_view(bar, snap, position=pos)) is True
    # Missing ATR and no frozen value -> False, never raise.
    assert AtrTakeProfit(k=1.0).evaluate(_view(bar, {}, position=pos)) is False
