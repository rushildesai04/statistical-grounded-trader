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
