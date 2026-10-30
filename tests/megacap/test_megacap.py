"""Point-in-time features, labels and walk-forward separation (prereg_megacap_brains_2026-10-30)."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from sgt.megacap import walkforward as wf
from sgt.megacap.features import DECISIONS, FEATURES, build


def bars(sessions, seed=0, scale=1.0):
    rng = np.random.default_rng(seed)
    rows = []
    for s in sessions:
        t0 = pd.Timestamp(f"{s} 09:30", tz="America/New_York").tz_convert("UTC")
        px = 100.0
        for k in range(390):
            px *= np.exp(rng.normal(0, 0.001))
            rows.append((t0 + pd.Timedelta(minutes=k), px * scale, px * scale * 1.001, px * scale * 0.999,
                         px * scale, 1000.0 / scale))
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])


SESS = [d.date() for d in pd.bdate_range("2024-01-02", periods=30)]


def daily_for(b):
    et = b.ts.dt.tz_convert("America/New_York")
    g = b.assign(d=et.dt.date).groupby("d")
    return pd.DataFrame({"open": g.open.first(), "high": g.high.max(), "low": g.low.min(), "close": g.close.last()})


def test_features_ignore_the_future_and_label_is_forward():
    b = bars(SESS)
    base = build(b, daily_for(b))
    assert list(base.columns[:3]) == ["session", "k", "label"] and set(FEATURES) <= set(base.columns)
    day = SESS[-1]
    t0 = pd.Timestamp(f"{day} 09:30", tz="America/New_York").tz_convert("UTC")
    b2 = b.copy()
    future = b2.ts >= t0 + pd.Timedelta(minutes=90)            # change everything from 11:00 on
    b2.loc[future, ["open", "high", "low", "close"]] *= 1.5
    alt = build(b2, daily_for(b))
    r1 = base[(base.session == day) & (base.k == 90)].iloc[0]
    r2 = alt[(alt.session == day) & (alt.k == 90)].iloc[0]
    for f in FEATURES:
        assert r1[f] == pytest.approx(r2[f], nan_ok=True), f
    g = b[b.ts.dt.tz_convert("America/New_York").dt.date == day].reset_index(drop=True)
    assert r1["label"] == pytest.approx(g.open[120] / g.open[90] - 1)
    last = base[(base.session == day) & (base.k == 360)].iloc[0]
    assert last["label"] == pytest.approx(g.close[389] / g.open[360] - 1)   # 15:30 -> session close


def test_dollar_volume_feature_is_split_invariant():
    a = build(bars(SESS), daily_for(bars(SESS)))
    b4 = bars(SESS)
    split_day = b4.ts.dt.tz_convert("America/New_York").dt.date >= SESS[25]
    b4.loc[split_day, ["open", "high", "low", "close"]] /= 4      # 4:1 split, raw prices
    b4.loc[split_day, "volume"] *= 4
    s = build(b4, daily_for(bars(SESS)))                          # adjusted daily unchanged
    ra, rs = a[a.session == SESS[27]], s[s.session == SESS[27]]
    assert np.allclose(ra.dvol_rel, rs.dvol_rel) and np.allclose(ra.r30, rs.r30, equal_nan=True)


def test_walk_forward_never_trains_on_test_month(monkeypatch):
    seen = []

    def fake(train, test, feats):
        seen.append((train.session.max(), test.session.min()))
        return np.linspace(-1, 1, len(test)), -0.5, 0.5

    monkeypatch.setattr(wf, "fit_predict", fake)
    monkeypatch.setattr(wf, "MIN_TRAIN_SESSIONS", 5)
    sessions = [d.date() for d in pd.bdate_range("2024-01-02", "2024-06-28")]
    df = pd.DataFrame({"session": np.repeat(sessions, 2), "label": 0.0, **{f: 0.0 for f in FEATURES}})
    out = wf.walk_forward(df, "2024-02")
    assert seen and all(tr < te for tr, te in seen)
    assert set(out.position.unique()) <= {-1, 0, 1}
    assert (out.position[out.pred >= 0.5] == 1).all() and (out.position[out.pred <= -0.5] == -1).all()


def test_portfolio_costs_and_averaging():
    t = pd.DataFrame({"session": [date(2024, 1, 2)] * 3, "position": [1, -1, 0], "label": [0.01, 0.01, 0.05]})
    u = pd.DataFrame({"session": [date(2024, 1, 2)], "position": [1], "label": [0.002]})
    p = wf.daily_portfolio({"A": t, "B": u}, {"A": 0.0004, "B": 0.001})
    a_day = (0.01 - 0.0004) + (-0.01 - 0.0004)
    b_day = 0.002 - 0.001
    assert p.iloc[0] == pytest.approx((a_day + b_day) / 2)


def test_half_day_has_no_decisions_after_close():
    b = bars(SESS)
    day = SESS[-1]
    et = b.ts.dt.tz_convert("America/New_York")
    b = b[~((et.dt.date == day) & (et.dt.hour >= 13))]          # 13:00 ET early close
    f = build(b, daily_for(b))
    ks = sorted(f[f.session == day].k)
    assert ks == [30, 60, 90, 120, 150, 180]                      # 10:00 .. 12:30
    g = b[b.ts.dt.tz_convert("America/New_York").dt.date == day].reset_index(drop=True)
    r = f[(f.session == day) & (f.k == 180)].iloc[0]
    assert r["label"] == pytest.approx(g.close.iloc[-1] / g.open[180] - 1)   # 12:30 -> 12:59 close


def test_real_early_close_uses_exchange_calendar():
    s = [date(2024, 11, 27), date(2024, 11, 29)]                   # 11-29 closes 13:00 ET
    b = bars(s)                                                    # full 390 bars both days
    f = build(b, daily_for(b))
    assert sorted(f[f.session == date(2024, 11, 29)].k) == [30, 60, 90, 120, 150, 180]
    assert len(f[f.session == date(2024, 11, 27)]) == 12


def test_daily_features_point_in_time():
    from sgt.megacap.daily import DAILY_FEATURES, build_daily

    sess = [d.date() for d in pd.bdate_range("2023-01-03", periods=300)]
    sess = [s for s in sess if s not in (date(2023, 1, 16), date(2023, 2, 20), date(2023, 4, 7), date(2023, 5, 29),
                                         date(2023, 6, 19), date(2023, 7, 4), date(2023, 9, 4), date(2023, 11, 23),
                                         date(2023, 12, 25), date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19))]
    b = bars(sess)
    d = daily_for(b).assign(volume=1e6)
    base = build_daily(b, d, b, d, b, d)
    assert set(DAILY_FEATURES) <= set(base.columns)
    t = sess[-20]
    i = list(d.index).index(t)
    assert base.set_index("session").label[t] == pytest.approx(d.close.iloc[i + 1] / d.close.iloc[i] - 1)
    d2 = d.copy()
    d2.loc[d2.index >= t, ["close", "high", "low"]] *= 1.3          # today's close and later
    b2 = b.copy()
    after = b2.ts >= pd.Timestamp(f"{t} 15:50", tz="America/New_York").tz_convert("UTC")
    b2.loc[after, ["open", "high", "low", "close"]] *= 1.3            # today after 15:49 and later days
    alt = build_daily(b2, d2, b2, d2, b2, d2)
    r1, r2 = base.set_index("session").loc[t], alt.set_index("session").loc[t]
    for f in DAILY_FEATURES:
        assert r1[f] == pytest.approx(r2[f], nan_ok=True), f


def test_walk_forward_accepts_a_feature_named_month():
    sessions = [d.date() for d in pd.bdate_range("2023-01-02", "2024-03-29")]
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"session": sessions, "month": [s.month for s in sessions], "x": rng.normal(size=len(sessions)),
                       "label": rng.normal(0, 0.01, len(sessions))})
    out = wf.walk_forward(df, "2024-02", features=["month", "x"],
                          params=dict(n_estimators=5, num_leaves=3, min_child_samples=5, verbose=-1))
    assert len(out) and out.month.between(1, 12).all()


def test_embargo_drops_last_session_before_test_month(monkeypatch):
    seen = []

    def fake(train, test, feats, params=None):
        seen.append(train.session.max())
        return np.zeros(len(test)), -1.0, 1.0

    monkeypatch.setattr(wf, "fit_predict", fake)
    monkeypatch.setattr(wf, "MIN_TRAIN_SESSIONS", 5)
    sessions = [d.date() for d in pd.bdate_range("2024-01-02", "2024-03-29")]
    df = pd.DataFrame({"session": sessions, "label": 0.0, **{f: 0.0 for f in FEATURES}})
    wf.walk_forward(df, "2024-02", params={"x": 1}, embargo_sessions=1)
    assert seen == [date(2024, 1, 30), date(2024, 2, 28)]   # Jan 31 and Feb 29 excluded
