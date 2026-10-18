"""Decision-model features: point-in-time invariance, trigger correctness, and a
compute_features sanity pass (docs/DECISION_MODEL.md §3, §4)."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

from sgt.core.interfaces import IndicatorSpec
from sgt.core.types import Bar, Segment
from sgt.decision.dataset import stream_candidates
from sgt.decision.features import (
    FEATURE_NAMES,
    CostModelAdapter,
    FeatureContext,
    compute_features,
)
from sgt.decision.triggers import TRIGGERS, fired_trigger
from sgt.strategy.base import EvalView, HistoryView

_T0 = datetime(2024, 3, 12, 14, 0, tzinfo=timezone.utc)
_SD = date(2024, 3, 12)


def _bar(i, o, h, l, c, v=100000.0) -> Bar:
    return Bar(symbol="AAAA", ts=_T0 + timedelta(minutes=i), open=o, high=h, low=l, close=c,
               volume=v, session_date=_SD, minute_of_day=600 + i, segment=Segment.RTH)


def _series() -> list[Bar]:
    bars = [_bar(i, 20.0, 20.3, 19.7, 20.0) for i in range(15)]
    bars.append(_bar(15, 20.0, 20.6, 19.9, 20.5))  # breakout -> hod_break
    c = 20.5
    for i in range(16, 40):
        c += 0.2
        bars.append(_bar(i, c - 0.2, c + 0.2, c - 0.3, c))
    return bars


def _nan_eq(a: float, b: float) -> bool:
    return (math.isnan(a) and math.isnan(b)) or a == b


# ------------------------------------------------------------------ point-in-time

def test_features_are_point_in_time():
    """Perturbing every bar strictly after a signal must not change the feature row at
    the signal (the streaming pipeline is causal)."""
    bars = _series()
    cost = CostModelAdapter()
    base = stream_candidates(bars, reference_fn=None, cost_source=cost, gate_session=None)
    assert base, "expected at least one candidate"
    target = base[0]
    sig = target.signal_idx
    # Some feature at the signal must be a real number (warm-up has passed for ATR etc).
    assert any(not math.isnan(v) for k, v in target.features.items()
               if k.startswith("f_")), "all features NaN - warm-up too short"

    perturbed = list(bars)
    for j in range(sig + 1, len(perturbed)):
        b = perturbed[j]
        perturbed[j] = _bar(j, b.open + 5.0, b.high + 7.0, b.low + 4.0, b.close + 6.0)
    after = stream_candidates(perturbed, reference_fn=None, cost_source=cost, gate_session=None)
    match = [c for c in after if c.signal_idx == sig]
    assert match, "signal candidate disappeared after perturbation"
    for name in FEATURE_NAMES:
        assert _nan_eq(target.features[name], match[0].features[name]), name


# ------------------------------------------------------------------ trigger correctness

def _k(spec: IndicatorSpec, out: str) -> str:
    return f"{spec.key}.{out}"


def _view(snap, prev, *, close=20.0, hod=None, pmh=None, hist_closes=()):
    bar = _bar(5, close, close + 0.1, close - 0.1, close)
    hv = HistoryView(list(hist_closes), [], [], [], [])
    return EvalView(bar=bar, snap_now=snap, snap_prev=prev, windows=frozenset({"rth"}),
                    history=hv, position=None, reference=None,
                    session_high_prior=hod, premarket_high=pmh)


def test_ema_cross_up_trigger():
    fk = _k(IndicatorSpec.of("ema", n=9), "value")
    sk = _k(IndicatorSpec.of("ema", n=24), "value")
    assert TRIGGERS["ema_cross_up"](_view({fk: 10.1, sk: 10.0}, {fk: 9.9, sk: 10.0}))
    assert not TRIGGERS["ema_cross_up"](_view({fk: 10.1, sk: 10.0}, {fk: 10.1, sk: 10.0}))


def test_macd_cross_up_trigger():
    m = IndicatorSpec.of("macd", fast=12, slow=26, signal=9)
    lk, sk = _k(m, "line"), _k(m, "signal")
    assert TRIGGERS["macd_cross_up"](_view({lk: 0.2, sk: 0.1}, {lk: 0.0, sk: 0.1}))
    assert not TRIGGERS["macd_cross_up"](_view({lk: 0.2, sk: 0.1}, {lk: 0.2, sk: 0.1}))


def test_vwap_reclaim_trigger():
    vk = _k(IndicatorSpec.of("vwap", anchor="session"), "value")
    # close 20 now > vwap 19.9; prev close 19.8 <= prev vwap 19.95 -> reclaim.
    v = _view({vk: 19.9}, {vk: 19.95}, close=20.0, hist_closes=[19.8])
    assert TRIGGERS["vwap_reclaim"](v)
    v2 = _view({vk: 19.9}, {vk: 19.95}, close=20.0, hist_closes=[20.1])  # already above
    assert not TRIGGERS["vwap_reclaim"](v2)


def test_hod_and_pmh_break_triggers():
    assert TRIGGERS["hod_break"](_view({}, {}, close=20.0, hod=19.5))
    assert not TRIGGERS["hod_break"](_view({}, {}, close=20.0, hod=20.5))
    assert TRIGGERS["pmh_break"](_view({}, {}, close=20.0, pmh=19.5))
    assert not TRIGGERS["pmh_break"](_view({}, {}, close=20.0, pmh=20.5))


def test_psar_flip_up_trigger():
    pk = _k(IndicatorSpec.of("psar"), "trend")
    assert TRIGGERS["psar_flip_up"](_view({pk: 1.0}, {pk: -1.0}))
    assert not TRIGGERS["psar_flip_up"](_view({pk: 1.0}, {pk: 1.0}))


def test_fired_trigger_priority_and_enabled():
    fk = _k(IndicatorSpec.of("ema", n=9), "value")
    sk = _k(IndicatorSpec.of("ema", n=24), "value")
    v = _view({fk: 10.1, sk: 10.0}, {fk: 9.9, sk: 10.0}, close=20.0, hod=19.5)
    assert fired_trigger(v) == "ema_cross_up"  # highest priority among fired
    assert fired_trigger(v, frozenset({"hod_break"})) == "hod_break"  # restricted


# ------------------------------------------------------------------ compute_features sanity

def test_compute_features_shape_and_onehots():
    bar = _bar(30, 20.0, 20.1, 19.9, 20.0)
    ctx = FeatureContext(
        bar=bar, snap={}, snap_prev=None, history=HistoryView([], [], [], [], []),
        reference=None, cost_frac=0.01, session_high_prior=None, premarket_high=None,
        bars_since_session_high=3, trigger="hod_break", windows=frozenset({"rth", "midday"}))
    feats = compute_features(ctx)
    assert set(FEATURE_NAMES).issubset(feats)
    assert feats["q_float_degraded"] == 1.0  # no reference -> degraded
    assert feats["f_seg_rth"] == 1.0 and feats["f_seg_pre"] == 0.0
    assert feats["f_trig_hod_break"] == 1.0 and feats["f_trig_ema_cross_up"] == 0.0
    assert feats["f_win_midday"] == 1.0
    assert feats["f_cost_rt_pct"] == 1.0  # 0.01 * 100
    assert feats["f_bars_since_hod"] == 3.0
    assert feats["f_minute_cos"] == math.cos(2 * math.pi * bar.minute_of_day / 1440.0)
