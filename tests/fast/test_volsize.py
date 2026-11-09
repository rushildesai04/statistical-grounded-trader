from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_volsize_study as vs  # noqa: E402


def test_shadow_weights_calm_skip_and_vol_up():
    w = vs.shadow_weights(pd.Series([0.010, vs.CALM, 0.05, 0.20, np.nan, 0.0]))
    assert w["calmskip"].tolist() == [0.0, 1.0, 1.0, 1.0, 1.0, 1.0]             # unknown / zero -> live rule
    assert np.allclose(w["volup"], [0.5, vs.CALM / vs.M, 0.05 / vs.M, 2.0, 1.0, 1.0])


class Store:
    def __init__(self, f):
        self.f = f

    def read_frame(self, symbol, start, end):
        return self.f


def bars(closes, start="2026-11-09 14:00"):
    ts = pd.date_range(start, periods=len(closes), freq="1min", tz="UTC")
    return pd.DataFrame({"ts": ts, "close": closes})


def test_sigma_uses_only_bars_closed_before_entry():
    calm = [10.0 * (1.001 ** (i % 2)) for i in range(40)]
    entry = pd.Timestamp("2026-11-09 14:40", tz="UTC")
    wild = calm + [20.0, 5.0, 30.0]                                               # after the entry: must be ignored
    assert vs.sigma(Store(bars(wild)), "X", "2026-11-09", entry) == vs.sigma(Store(bars(calm)), "X", "2026-11-09", entry)
    assert vs.sigma(Store(bars(calm[:8])), "X", "2026-11-09", entry) is None    # < 10 returns: unknown
    assert vs.sigma(Store(pd.DataFrame()), "X", str(date(2026, 11, 9)), entry) is None


def test_summary_lines():
    t = pd.DataFrame({"net": [-0.02, 0.04], "w": [0.0, 1.0]})
    assert "skipped 1 calm (-2.0% avg), kept +4.00%" in vs.summary_line("calmskip", t)
    assert "vol-up" in vs.summary_line("volup", t.assign(w=[0.5, 2.0]))


def test_ev_weights_follow_the_frozen_model_and_skip_news():
    t = pd.DataFrame({"entry_ts": ["2026-11-10T19:30:00+00:00", "2026-11-10T14:40:00+00:00", "2026-11-10T19:30:00+00:00"],
                      "entry": [1.5, 3.0, 1.5], "sigma": [0.05, 0.02, 0.05],
                      "alert_type": ["new_high", "volume_spike", "news"]})
    w = vs.ev_weights(t)
    assert w[0] > 1.5 and w[1] < 0.6 and w[2] == 1.0              # jumpy afternoon $1-2 up; mid morning $2-5 down
    assert ((w >= 0.25 * 0.9) & (w <= 2.0 * 1.1)).all()
