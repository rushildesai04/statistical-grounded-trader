"""The entry-timing study's arithmetic (long_entry_timing_2026-11-20), on made-up candles."""
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_entry_timing_study as st  # noqa: E402

from sgt.fast import patterns as pt  # noqa: E402

T1 = pd.Timestamp("2026-06-09 14:01", tz="UTC")
SPIKE, QUIET, BREAK = (10.0, 10.5, 10.0, 10.4, 900), (10.35, 10.45, 10.25, 10.35, 100), (10.4, 10.65, 10.4, 10.6, 500)
FILL, RUN, AFTER = (10.6, 10.7, 10.55, 10.65, 300), (10.65, 11.4, 10.6, 11.35, 800), (11.3, 11.4, 11.2, 11.3, 100)


def bars(rows):
    return pd.DataFrame([{"ts": T1 + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                         for i, (o, h, lo, c, v) in enumerate(rows)])


DAY = bars([SPIKE] + [QUIET] * 11 + [BREAK, FILL, RUN] + [AFTER] * 40)       # break candle 14:13, fill candle 14:14


def cost(sym, ts, px):
    return 0.01


def test_the_buy_is_moved_in_time_with_the_same_stop():
    tape = DAY.copy()
    tape.loc[14, "open"] = 10.8                                  # one minute late the tape is higher
    o = st.shifted(DAY, tape, "ABCD", "2026-06-09", T1 + 13 * pt.BAR, cost)
    assert (o["wait_min"], o["base_top"], o["stop"], o["break_close"], o["tape_at_close"]) == (0, 10.45, 10.25, 10.6, 10.6)
    assert o["at_close_entry"] == o["fill_candle_entry"] == 10.6 and o["late1_entry"] == 10.8
    assert o["at_close_net"] == pytest.approx(2 * (10.6 - 10.25) / 10.6 - 0.01)          # 2R target 11.30 next minute
    assert o["late1_net"] == pytest.approx(11.3 / 10.8 - 1 - 0.01)                      # 30 minutes later, no target
    assert o["buy_stop_confirmed_entry"] == 10.45                # filled inside the break candle at the base top


def test_a_quiet_feed_makes_the_book_wait_and_the_close_buy_does_not():
    feed = DAY.drop(index=[13]).reset_index(drop=True)           # the feed has no candle in the minute after the break
    o = st.shifted(feed, DAY, "ABCD", "2026-06-09", T1 + 14 * pt.BAR, cost)
    assert o["wait_min"] == 1 and o["at_close_entry"] == 10.6 and o["fill_candle_entry"] == 10.65


def test_the_scan_finds_the_close_rule_and_the_resting_buy_stop():
    o = st.scan(DAY, DAY, "ABCD", "2026-06-09", T1, cost)
    assert (o["c_entry"], o["c_ts"]) == (10.6, T1 + 13 * pt.BAR)
    assert (o["s_entry"], o["s_ts"]) == (10.45, T1 + 11 * pt.BAR)                       # a touch of the top fills it
    o = st.scan(DAY, DAY, "ABCD", "2026-06-09", T1, cost, above=0.01)
    assert o["s_entry"] == pytest.approx(10.46) and o["s_ts"] == T1 + 12 * pt.BAR       # a cent above: the break candle


def test_a_wick_fills_the_buy_stop_only():
    wick = (10.35, 10.5, 10.3, 10.35, 100)                       # trades above the base, closes inside it
    day = bars([SPIKE] + [QUIET] * 11 + [wick] + [(10.3, 10.35, 10.2, 10.25, 200)] + [(10.2, 10.25, 10.1, 10.2, 50)] * 60)
    o = st.scan(day, day, "ABCD", "2026-06-09", T1, cost)
    assert "c_net" not in o and o["s_entry"] == 10.45
    assert o["s_net"] == pytest.approx(10.25 / 10.45 - 1 - 0.01)                        # stopped under the base
