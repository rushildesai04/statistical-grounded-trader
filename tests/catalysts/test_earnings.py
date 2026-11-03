"""Earnings events (src/sgt/catalysts/earnings.py): EDGAR parsing, reaction session, dedupe, trade math."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sgt.catalysts import earnings as er

ET = ZoneInfo("America/New_York")
SESS = pd.DatetimeIndex(pd.bdate_range("2024-01-02", periods=60))   # Mon-Fri stand-in calendar


def test_acceptance_is_utc_converted_to_eastern():
    assert er.parse_acceptance("2023-11-02T20:30:28.000Z") == datetime(2023, 11, 2, 16, 30, 28, tzinfo=ET)
    assert er.parse_acceptance("2024-07-30T20:30:00.000Z").hour == 16       # EDT
    with pytest.raises(ValueError):
        er.parse_acceptance("garbage")


def test_only_plain_8k_with_item_202():
    block = {"form": ["8-K", "8-K/A", "8-K", "10-Q"], "items": ["2.02,9.01", "2.02", "5.02", ""],
             "acceptanceDateTime": ["2024-01-25T21:05:00.000Z"] * 4, "accessionNumber": ["a", "b", "c", "d"]}
    assert [r["accession"] for r in er.earnings_rows(block)] == ["a"]
    with pytest.raises(ValueError):
        er.earnings_rows({**block, "items": ["2.02"]})


def test_reaction_session_rules():
    wed = pd.Timestamp("2024-01-03")
    assert er.reaction_session(datetime(2024, 1, 3, 7, 0, tzinfo=ET), SESS) == wed                # pre-market
    assert er.reaction_session(datetime(2024, 1, 3, 16, 5, tzinfo=ET), SESS) == pd.Timestamp("2024-01-04")
    assert er.reaction_session(datetime(2024, 1, 3, 11, 0, tzinfo=ET), SESS) is None                # in RTH
    assert er.reaction_session(datetime(2024, 1, 6, 10, 0, tzinfo=ET), SESS) == pd.Timestamp("2024-01-08")  # Saturday


def test_repeat_filings_within_20_sessions_are_dropped():
    raw = pd.DataFrame({"accepted": [datetime(2024, 1, 3, 7, tzinfo=ET), datetime(2024, 1, 10, 7, tzinfo=ET),
                                     datetime(2024, 2, 14, 7, tzinfo=ET)]})
    assert list(er.events(raw, SESS).R) == [pd.Timestamp("2024-01-03"), pd.Timestamp("2024-02-14")]


def prices():
    idx = SESS[:10]
    px = pd.DataFrame({"open": 100.0, "close": 100.0}, index=idx)
    spy = pd.DataFrame({"open": 400.0, "close": 400.0}, index=idx)
    R = idx[2]
    px.loc[R, "open"], px.loc[R, "close"], px.loc[idx[3], "close"] = 105.0, 108.0, 110.0   # +5% gap, drifts up
    spy.loc[idx[3], "close"] = 404.0                                                       # market +1% by R+1
    return er.event_prices(pd.DataFrame({"R": [R]}), px, spy)


def test_continuation_and_reversal_are_hedged_and_costed():
    p = prices()
    cont = er.config_trades(p, er.CONFIGS["open_cont_m3%_x1"])
    assert cont.side.iloc[0] == 1
    assert cont.net.iloc[0] == pytest.approx((110 / 105 - 1) - (404 / 400 - 1) - er.COST)
    rev = er.config_trades(p, er.CONFIGS["open_rev_m1%_x0"])
    assert rev.side.iloc[0] == -1 and rev.net.iloc[0] == pytest.approx(-(108 / 105 - 1) - er.COST)
    assert er.config_trades(p, er.CONFIGS["close_cont_m5%_x1"]).net.iloc[0] == pytest.approx(
        (110 / 108 - 1) - 0.01 - er.COST)                                  # close-to-close +8% >= 5%
    assert er.config_trades(p.assign(open_R=100.5), er.CONFIGS["open_cont_m1%_x0"]).empty   # gap below filter


def test_events_without_full_window_are_dropped_and_config_count():
    idx = SESS[:4]
    px = pd.DataFrame({"open": 1.0, "close": 1.0}, index=idx)
    assert er.event_prices(pd.DataFrame({"R": [idx[2]]}), px, px).empty
    assert len(er.CONFIGS) == 20


def test_date_bootstrap_lower_bound_clusters_by_date():
    t = pd.DataFrame({"R": [1, 1, 2, 3], "net": [0.01, 0.01, 0.02, 0.03]})
    lb = er.date_lower_bound(t, 0.05, np.random.default_rng(0), n_boot=2000)
    assert 0.01 <= lb <= t.net.mean()
