from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sgt.crypto import leadlag as ll

T0 = pd.Timestamp("2024-01-01", tz="UTC")


def frame(closes, start=T0, opens=None):
    ts = pd.date_range(start, periods=len(closes), freq="min")
    c = np.asarray(closes, float)
    return pd.DataFrame({"ts": ts, "open": c if opens is None else opens, "close": c})


def test_events_are_point_in_time_need_a_full_window_and_respect_the_gap():
    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.0005, 3000)
    r[[100, 2000, 2005]] = [0.02, 0.02, -0.02]                            # 100 is inside the warm-up window
    btc = frame(100 * np.exp(np.cumsum(r)))
    ev = ll.btc_events(btc, q=0.995, window=1000, gap=10)
    m = ((ev.ts - T0) / pd.Timedelta(minutes=1)).astype(int).tolist()
    assert 2000 in m and 2005 not in m and min(m) > 1000                   # shock kept; warm-up and gap respected
    assert (np.diff(m) > 10).all() and (ev.thr > 0).all()
    assert ev.set_index(ev.ts - T0).ret.loc[pd.Timedelta(minutes=2000)] > 0


def test_basket_enters_next_open_follows_btc_sign_and_skips_alts_without_a_candle():
    e = pd.DataFrame({"ts": [T0], "ret": [-0.01]})
    up = frame([10, 10, 10, 10, 10, 9, 9], opens=[10, 10, 10, 10, 10, 9, 9])  # falls to 9 at t+5
    gone = frame([5, 5], start=T0 + pd.Timedelta(minutes=3))                   # no candle at t+1
    out = ll.basket(e, {"A": up, "B": gone}, hold=5, cost=0.001).iloc[0]
    assert out.n_alts == 1 and out.gross == pytest.approx(0.10) and out.net == pytest.approx(0.099)


def test_gate_needs_size_positive_bound_halves_and_no_dominant_month():
    rng = np.random.default_rng(0)
    ts = pd.date_range("2024-02-01", periods=400, freq="37h", tz="UTC")
    good = pd.DataFrame({"ts": ts, "net": rng.normal(0.002, 0.003, 400), "gross": 0.0})
    mid = pd.Timestamp("2025-03-01", tz="UTC")
    assert ll.gate(good, mid, np.random.default_rng(0))["pass"]
    assert not ll.gate(good.iloc[:150], mid, np.random.default_rng(0))["pass"]          # < 200 events
    late = good.assign(net=np.where(good.ts < mid, -0.002, 0.006))
    assert not ll.gate(late, mid, np.random.default_rng(0))["pass"]                     # first half negative
    assert ll.gate(good.iloc[:0], mid, np.random.default_rng(0)) == {"n": 0, "pass": False}
