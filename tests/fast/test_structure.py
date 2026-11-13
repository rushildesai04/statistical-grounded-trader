"""Swing-pivot BoS / CHoCH (prereg_bos_ema_2026-11-13): hand-built bars, causality, rule mutations fail."""
from __future__ import annotations

import numpy as np
import pandas as pd

from sgt.fast import structure as st


def frame(highs, lows=None, closes=None):
    h = np.asarray(highs, float)
    lo = h - 0.5 if lows is None else np.asarray(lows, float)
    c = h - 0.1 if closes is None else np.asarray(closes, float)
    return pd.DataFrame({"ts": pd.date_range("2026-11-12 15:00", periods=len(h), freq="1min", tz="UTC"),
                         "open": c, "high": h, "low": lo, "close": c, "volume": 1000.0})


# flat 10s, a pivot high 12 at bar 6, a pivot low (low 8) at bar 13, then closes above 12 at bar 20 and above the
# next pivot high (13 at bar 21) at bar 28
H = [10, 10, 10, 10, 10, 10, 12, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 10, 12.5, 13, 11, 11, 11, 11, 11, 11,
     13.5, 13.4]
L = [9.5] * 13 + [8] + [9.5] * 16
C = [h - 0.1 for h in H]


def test_first_break_is_a_choch_then_a_bos_with_the_pivot_low_as_stop():
    b = st.bullish_breaks(frame(H, L, C))
    assert [(x.i, x.kind, x.level, x.stop) for x in b] == [(20, "CHOCH", 12.0, 8.0), (28, "BOS", 13.0, 8.0)]


def test_a_pivot_is_not_usable_before_it_is_confirmed():
    h = [10] * 6 + [12] + [10] * 3 + [12.5] + [10] * 8   # bar 10 closes above 12, but bar 6 confirms only at bar 11
    assert st.bullish_breaks(frame(h)) == []             # (and by bar 11 the close is back below: no signal at all)


def test_each_pivot_gives_one_signal_and_equal_close_is_no_break():
    h = H[:20] + [12.1, 12.1, 12.1]
    c = C[:20] + [12.0, 12.05, 12.08]                    # 12.0 == level: no break; then one break, not two
    assert [x.i for x in st.bullish_breaks(frame(h, L[:20] + [9.5] * 3, c))] == [21]


def test_a_bearish_break_turns_the_next_bullish_break_into_a_choch():
    h = H + [11, 11, 11, 11, 11, 14, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 11, 14.5]
    lo = L + [9.5] * 18
    c = C + [10.9] * 5 + [13.9] + [7.5] + [10.9] * 10 + [14.4]   # bar 36 closes below the pivot low 8
    kinds = [(x.i, x.kind) for x in st.bullish_breaks(frame(h, lo, c))]
    assert kinds[:2] == [(20, "CHOCH"), (28, "BOS")] and kinds[-1] == (47, "CHOCH")


def test_no_look_ahead_every_prefix_agrees():
    rng = np.random.default_rng(7)
    h = 10 + np.cumsum(rng.normal(0, 0.3, 300))
    f = frame(h, h - rng.uniform(0.1, 0.6, 300), h - rng.uniform(0, 0.1, 300))
    full = st.bullish_breaks(f)
    assert len(full) > 5
    for k in range(20, 300):
        assert st.bullish_breaks(f.iloc[:k]) == [b for b in full if b.i < k]
