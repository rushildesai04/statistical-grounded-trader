"""Learning-from-results diagnostics tests (plan §9, §10, §11.2).

Deterministic: hand-built bar series and synthetic sessions only, no network.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from sgt.brokers.costs import CostModel
from sgt.core.types import Bar, Segment
from sgt.intraday.calendar import default_calendar
from sgt.intraday.synthetic import Ignition, make_multi_session
from sgt.learning import diagnostics
from sgt.learning.diagnostics import (
    bucket_table,
    cost_attribution,
    learned_filter_walkforward,
    signal_outcomes,
    stable_buckets,
)
from sgt.learning.diagnostics_report import (
    DEFAULT_BUCKET_SPECS,
    recommendations,
    render_report,
)
from sgt.strategy.loader import load_strategy

_UTC = timezone.utc
_CM = CostModel()  # default: $5-20 bucket half-spread == 0.0020


def _bars(symbol, session_date, opens, closes, *, start_min=570, base=None):
    """Build a bar series with prices kept in the $5-20 bucket (half-spread 0.0020)."""
    if base is None:
        base = datetime(2026, 9, 1, 14, 30, tzinfo=_UTC)
    bars = []
    for i, (o, c) in enumerate(zip(opens, closes)):
        hi = max(o, c) + 0.01
        lo = min(o, c) - 0.01
        bars.append(Bar(symbol=symbol, ts=base + timedelta(minutes=i), open=o, high=hi,
                        low=lo, close=c, volume=10_000.0, session_date=session_date,
                        minute_of_day=start_min + i, segment=Segment.RTH))
    return bars


def _cands(symbol, rows):
    """rows: list of (ts, features) -> candidate frame like run_backtest emits."""
    return pd.DataFrame(
        [{"ts": pd.Timestamp(ts), "symbol": symbol, "taken": True,
          "skip_reason": "", "features": feats} for ts, feats in rows],
        columns=["ts", "symbol", "taken", "skip_reason", "features"])


# --------------------------------------------------------------- forward outcome math


def test_forward_outcome_math_next_bar_entry_and_horizon_exit():
    opens = [10.0] * 10
    closes = [10.0, 10.05, 10.10, 10.15, 10.20, 10.25, 10.30, 10.35, 10.40, 10.45]
    bars = _bars("AAAA", date(2026, 9, 1), opens, closes)
    cands = _cands("AAAA", [(bars[0].ts, {"minute_of_day": 570})])

    out = signal_outcomes(cands, lambda s: bars, horizons=(5, 15, 30), cost_model=_CM)
    assert len(out) == 1
    r = out.iloc[0]

    hs = 0.0020
    entry = 10.0 * (1 + hs)          # next bar (index 1) open + half-spread
    assert r.price == pytest.approx(10.0)
    assert r.cost_rt == pytest.approx(2 * hs)
    # h=5 -> bar index 6 close 10.30; h=15/30 clamp to last bar (index 9) close 10.45
    assert r.net5 == pytest.approx(10.30 * (1 - hs) / entry - 1)
    assert r.gross5 == pytest.approx(10.30 / 10.0 - 1)
    assert r.net15 == pytest.approx(10.45 * (1 - hs) / entry - 1)
    assert r.net30 == pytest.approx(r.net15)       # both clamp to last bar
    assert r.mfe30 == pytest.approx(10.46 / 10.0 - 1)   # max high over window
    assert r.mae30 == pytest.approx(9.99 / 10.0 - 1)    # min low over window
    assert r.minute_of_day == 570
    assert r.session_date == date(2026, 9, 1)


def test_no_cross_session_exit_and_no_overnight_entry():
    s1, s2 = date(2026, 9, 1), date(2026, 9, 2)
    b1 = _bars("BBBB", s1, [10.0] * 5, [10.0, 10.1, 10.2, 10.3, 10.4], start_min=570,
               base=datetime(2026, 9, 1, 14, 30, tzinfo=_UTC))
    b2 = _bars("BBBB", s2, [11.0] * 5, [11.0, 11.1, 11.2, 11.3, 11.4], start_min=570,
               base=datetime(2026, 9, 2, 14, 30, tzinfo=_UTC))
    bars = b1 + b2

    # Signal at s1 index 3 -> entry s1 index 4 (last s1 bar); h=5 would land in s2,
    # must clamp back to the last same-session bar (index 4 itself).
    cands = _cands("BBBB", [(b1[3].ts, {"minute_of_day": 573})])
    out = signal_outcomes(cands, lambda s: bars, horizons=(5,), cost_model=_CM)
    assert len(out) == 1
    hs = 0.0020
    entry = b1[4].open * (1 + hs)
    assert out.iloc[0].net5 == pytest.approx(b1[4].close * (1 - hs) / entry - 1)

    # Signal at the LAST s1 bar -> entry would be first s2 bar (overnight) -> dropped.
    cands2 = _cands("BBBB", [(b1[4].ts, {"minute_of_day": 574})])
    out2 = signal_outcomes(cands2, lambda s: bars, horizons=(5,), cost_model=_CM)
    assert out2.empty


# --------------------------------------------------------------- feature extraction


def test_feature_extraction_by_key_and_prefix_with_missing():
    bars = _bars("CCCC", date(2026, 9, 1), [10.0] * 8, [10.0] * 8)
    full = {
        "minute_of_day": 900, "close": 10.0,
        "rsi(n=14).value": 55.0,
        "ema(n=9).value": 10.1, "ema(n=24).value": 10.0,
        "pvt(signal_kind=sma,signal_n=14).pvt": 5.0,
        "pvt(signal_kind=sma,signal_n=14).signal": 4.0,
        "psar().trend": 1.0,
        "gap_pct": 2.0, "dist_hod_pct": -1.0,
        "free_float": 3_000_000.0, "dilution_flag": 1.0, "bar_volume": 50_000.0,
        "relvol(lookback_sessions=20).value": 4.0,
        "relvol(lookback_sessions=20).cum": 2.5,
    }
    sparse = {"minute_of_day": 600}
    cands = _cands("CCCC", [(bars[0].ts, full), (bars[2].ts, sparse)])
    out = signal_outcomes(cands, lambda s: bars, horizons=(5,), cost_model=_CM)

    a = out[out.ts == pd.Timestamp(bars[0].ts)].iloc[0]
    assert a.rsi == 55.0
    assert a.ema_spread == pytest.approx((10.1 - 10.0) / 10.0)
    assert a.dist_hod == -1.0 and a.gap == 2.0
    assert a.pvt_above == 1.0 and a.psar_up == 1.0
    assert a.free_float == 3_000_000.0 and a.dilution == 1.0
    assert a.bar_volume == 50_000.0
    assert a.relvol_value == 4.0 and a.relvol_cum == 2.5
    assert a.minute_of_day == 900

    b = out[out.ts == pd.Timestamp(bars[2].ts)].iloc[0]
    for col in ("rsi", "ema_spread", "dist_hod", "gap", "pvt_above", "psar_up",
                "free_float", "dilution", "bar_volume", "relvol_value", "relvol_cum"):
        assert pd.isna(b[col]), f"{col} should be NaN when its feature is absent"
    assert b.minute_of_day == 600


# --------------------------------------------------------------- bucketing / stability


def _outcomes_for_stability():
    rows = []
    half1 = [date(2026, 9, 1), date(2026, 9, 2)]
    half2 = [date(2026, 9, 3), date(2026, 9, 4)]
    # bucket 1.0: positive in BOTH halves, n>=min_n each.
    for d in half1 + half2:
        for _ in range(3):
            rows.append({"session_date": d, "pvt_above": 1.0, "net30": 0.01})
    # bucket 0.0: positive in half1 only (negative in half2).
    for d in half1:
        for _ in range(3):
            rows.append({"session_date": d, "pvt_above": 0.0, "net30": 0.02})
    for d in half2:
        for _ in range(3):
            rows.append({"session_date": d, "pvt_above": 0.0, "net30": -0.02})
    return pd.DataFrame(rows)


def test_stable_buckets_require_both_halves_positive():
    out = _outcomes_for_stability()
    st = stable_buckets(out, [("pvt_above", None)], min_n=2)
    flags = {r.bucket: r.stable for _, r in st.iterrows()}
    assert flags["1.0"] is True
    assert flags["0.0"] is False
    # both-halves bucket reports per-half counts and means
    pos = st[st.bucket == "1.0"].iloc[0]
    assert pos.n_h1 == 6 and pos.n_h2 == 6  # 2 sessions x 3 rows per half
    assert pos.net_h1 == pytest.approx(0.01) and pos.net_h2 == pytest.approx(0.01)


def test_stable_buckets_min_n_blocks_thin_bucket():
    out = _outcomes_for_stability()
    st = stable_buckets(out, [("pvt_above", None)], min_n=7)  # n=6 per half < 7
    assert not st[st.stable].shape[0]


def test_bucket_table_cuts_and_reports_win_rate():
    df = pd.DataFrame({"rsi": [10, 40, 65, 75], "net30": [0.01, -0.01, 0.02, 0.03],
                       "cost_rt": [0.004] * 4})
    tbl = bucket_table(df, "rsi", bins=[0, 50, 101], metric="net30")
    assert list(tbl.columns) == ["n", "mean", "win", "cost_rt"]
    assert tbl["n"].sum() == 4


# --------------------------------------------------------------- recommendations


def test_recommendations_no_trade_when_gross_within_cost():
    n = 40
    out = pd.DataFrame({
        "gross30": [0.0005] * n, "net30": [-0.0035] * n, "cost_rt": [0.004] * n,
        "session_date": [date(2026, 9, 1)] * n,
    })
    recs = recommendations(out, pd.DataFrame(), pd.DataFrame())
    assert any("NO-TRADE" in r for r in recs)
    assert any("No stable positive buckets" in r for r in recs)


def test_recommendations_flag_learned_filter_no_edge():
    out = pd.DataFrame({"gross30": [0.02] * 10, "net30": [0.01] * 10,
                        "cost_rt": [0.004] * 10, "session_date": [date(2026, 9, 1)] * 10})
    wfo = pd.DataFrame({"top_net": [-0.001, -0.002], "model": ["logistic", "gbm"]})
    recs = recommendations(out, pd.DataFrame(), wfo)
    assert any("top-decile net <= 0" in r for r in recs)


# --------------------------------------------------------------- learned filter WFO


def _wfo_outcomes(n_sessions=8, per_session=4):
    base = date(2026, 9, 1)
    rows = []
    for k in range(n_sessions):
        d = base + timedelta(days=k)
        for j in range(per_session):
            net = 0.01 if j % 2 == 0 else -0.01  # both classes each session
            rows.append({
                "session_date": d, "symbol": "AAAA",
                "minute_of_day": 600 + j, "price": 10.0 + j, "rsi": 40.0 + j,
                "ema_spread": 0.001 * j, "dist_hod": -1.0 * j, "gap": 0.5 * j,
                "pvt_above": float(j % 2), "psar_up": float((j + 1) % 2),
                "free_float": 1e7, "dilution": 0.0, "bar_volume": 1e5,
                "cost_rt": 0.004, "net30": net,
            })
    return pd.DataFrame(rows)


def test_walkforward_never_trains_on_test_sessions(monkeypatch):
    out = _wfo_outcomes()
    session_of = out["session_date"].to_dict()
    dates = sorted(pd.unique(out["session_date"]))

    class _FakeFilter:
        calls: list[set] = []

        def __init__(self, *a, **k):
            pass

        def fit(self, X, y, **k):
            _FakeFilter.calls.append({session_of[i] for i in X.index})
            return self

        def score(self, feats):
            return 0.5

    _FakeFilter.calls = []
    monkeypatch.setattr(diagnostics, "LogisticMetaFilter", _FakeFilter)
    monkeypatch.setattr(diagnostics, "GBMMetaFilter", _FakeFilter)

    wfo = learned_filter_walkforward(out, folds=3)
    assert not wfo.empty
    assert _FakeFilter.calls, "fit was never called"
    for trained in _FakeFilter.calls:
        m = len(trained)
        assert trained == set(dates[:m]), "training set is not a chronological prefix"
        assert m < len(dates), "training set must leave future sessions unseen"


def test_walkforward_deterministic_and_shaped():
    out = _wfo_outcomes()
    a = learned_filter_walkforward(out, folds=3)
    b = learned_filter_walkforward(out, folds=3)
    assert list(a.columns) == ["fold", "model", "train_sessions", "test_sessions",
                               "n_train", "n_test", "auc", "all_net", "top_n",
                               "top_net", "top_win"]
    pd.testing.assert_frame_equal(a, b)
    assert set(a["model"]) == {"logistic", "gbm"}


# --------------------------------------------------------------- report rendering


def test_render_report_has_all_sections():
    out = _wfo_outcomes()
    out["gross30"] = 0.02
    st = stable_buckets(out, DEFAULT_BUCKET_SPECS, min_n=2)
    wfo = learned_filter_walkforward(out, folds=3)
    recs = recommendations(out, st, wfo)
    md = render_report(outcomes=out, cost_model=_CM, recommendations=recs,
                       cost_attr=None, stable=st, walkforward=wfo, as_of="2026-10-17")
    for header in ("# Learning from Results", "## Data coverage",
                   "## Cost-model assumptions", "## Cost attribution (gross vs net)",
                   "## Overall forward outcomes", "## Context buckets",
                   "## Stability", "## Learned-filter walk-forward",
                   "## Recommendations"):
        assert header in md, f"missing section: {header}"
    assert "Trigger signals studied:" in md


# --------------------------------------------------------------- cost attribution (integration)


def test_cost_attribution_and_signal_outcomes_end_to_end():
    config = load_strategy("strategies/trader_v0.yaml")
    sess = default_calendar().previous_sessions(date(2024, 3, 15), 2)
    igs = (Ignition(start_minute=70, length=15, drift_per_bar=0.02, volume_mult=12.0),)
    bars_by_symbol = {}
    for i, sym in enumerate(["WFFF", "YMAT"]):
        bars_by_symbol[sym] = make_multi_session(
            sym, sess, seed=5 + i, start_price=6.0 + i,
            segments=(Segment.RTH,), ignitions=igs)

    attr = cost_attribution(config, bars_by_symbol, reference=None,
                            allow_unknown_float=True, cost_model=_CM)
    assert not attr.take_every_candidates.empty
    te = attr.table.loc["take_every"]
    # Zero-cost gross expectancy can never be worse than net-of-costs.
    assert te["gross_exp"] >= te["net_exp"] - 1e-9

    out = signal_outcomes(attr.take_every_candidates,
                          lambda s: bars_by_symbol.get(s.upper(), ()), cost_model=_CM)
    assert not out.empty
    assert out["net30"].mean() <= out["gross30"].mean() + 1e-9
