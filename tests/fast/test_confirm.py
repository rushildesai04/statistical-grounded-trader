from __future__ import annotations

import pandas as pd
import pytest

from sgt.fast import confirm
from sgt.fast.exits import indicators

T0 = pd.Timestamp("2026-11-06T14:42:00Z")


def ind(rows):
    """rows: (open, high, low, close) per minute from T0."""
    return indicators(pd.DataFrame({"ts": [T0 + pd.Timedelta(minutes=i) for i in range(len(rows))],
                                    "open": [r[0] for r in rows], "high": [r[1] for r in rows],
                                    "low": [r[2] for r in rows], "close": [r[3] for r in rows],
                                    "volume": [1000.0] * len(rows)}))


def test_confirm_needs_a_breakdown_then_a_red_bounce_below_the_high():
    rows = [(10, 10.5, 9.9, 10.4), (10.4, 10.6, 10.0, 10.1),     # H = 10.6; 10.1 <= 10.6*0.97 = 10.28: breakdown
            (10.1, 10.5, 10.0, 10.4),                             # green bounce: not a fail
            (10.4, 10.55, 10.2, 10.25),                           # red, high < H: failed bounce -> enter next open
            (10.2, 10.3, 9.9, 10.0)] + [(10.0, 10.1, 9.5, 9.6)] * 70
    t, px = confirm.confirm_entry(ind(rows), T0)
    assert t == T0 + pd.Timedelta(minutes=4) and px == 10.2
    assert confirm.confirm_gross(ind(rows), T0) == pytest.approx((10.2 - 9.6) / 10.2)


def test_confirm_skips_a_stock_that_never_breaks_down():
    rows = [(10 + i * 0.1, 10.2 + i * 0.1, 9.95 + i * 0.1, 10.1 + i * 0.1) for i in range(40)]   # RAYA-like grind up
    assert confirm.confirm_entry(ind(rows), T0) is None and confirm.confirm_gross(ind(rows), T0) is None


def test_failed_pullback_covers_when_close_breaks_the_post_entry_high():
    rows = [(1.52, 1.61, 1.36, 1.46),                             # entry bar (RAYA 09:43 shape), high 1.61
            (1.47, 1.51, 1.40, 1.41),                             # close 1.41 <= 1.52*0.97: pullback in our favor
            (1.40, 1.55, 1.40, 1.545),                            # close 1.545 < 1.61: not yet
            (1.54, 1.79, 1.50, 1.73),                             # close 1.73 > 1.61: pullback failed -> next open
            (1.73, 1.80, 1.66, 1.79)] + [(1.8, 1.82, 1.7, 1.75)] * 60
    g, early = confirm.failpb_gross(ind(rows), T0, 1.52)
    assert early and g == pytest.approx((1.52 - 1.73) / 1.52)    # -13.8% instead of the 20% stop


def test_failed_pullback_without_a_pullback_keeps_the_champion():
    rows = [(10, 10.2, 9.9, 10.0)] * 70
    g, early = confirm.failpb_gross(ind(rows), T0, 10.0)
    assert not early and g == pytest.approx(0.0)
