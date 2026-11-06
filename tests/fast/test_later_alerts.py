from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import fade_later_study as fl  # noqa: E402


def test_later_events_skip_the_first_and_any_alert_inside_the_previous_trade():
    t0 = pd.Timestamp("2025-03-03T15:00:00Z")
    prim = pd.DataFrame({"symbol": ["A"] * 5 + ["B"], "session": ["2025-03-03"] * 6,
                         "posted_at": [t0 + pd.Timedelta(minutes=m) for m in (0, 20, 61, 90, 125, 5)],
                         "entry_ts": [t0 + pd.Timedelta(minutes=m) for m in (0, 20, 61, 90, 125, 5)]})
    ev = fl.later_events(prim)
    # A: first at 0 kept (not returned); 20 inside it; 61 kept (2nd); 90 inside 61's hour; 125 kept (3rd). B: first only
    assert list(zip(ev.symbol, ev.alert_no)) == [("A", 2), ("A", 3)]
    assert [int((t - t0).total_seconds() // 60) for t in ev.entry_ts] == [61, 125]


def test_realert_covers_at_the_reaction_bar_unless_stopped_or_after_the_exit():
    import fade_realert_study as fr
    from sgt.fast.exits import indicators

    t0 = pd.Timestamp("2025-03-03T15:00:00Z")
    closes = [10.0, 10.2, 10.5, 10.8, 11.0, 11.2] + [11.5] * 70
    ind = indicators(pd.DataFrame({"ts": [t0 + pd.Timedelta(minutes=m) for m in range(len(closes))], "open": closes,
                                   "high": closes, "low": closes, "close": closes, "volume": [1000.0] * len(closes)}))
    g, re = fr.realert_gross(ind, t0, 10.0, [t0 + pd.Timedelta(minutes=2, seconds=55)])   # +10 s -> bar 4 (11.0)
    assert re and abs(g - (10.0 - 11.0) / 10.0) < 1e-12
    g, re = fr.realert_gross(ind, t0, 10.0, [t0 + pd.Timedelta(minutes=70)])               # after the 60-min exit
    assert not re and abs(g - (10.0 - 11.5) / 10.0) < 1e-12
    g, re = fr.realert_gross(ind, t0, 10.0, [t0 - pd.Timedelta(minutes=1)])                # before entry: ignored
    assert not re
