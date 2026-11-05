from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from sgt.fast import news

ALERT = datetime(2026, 11, 5, 15, 11, 54, tzinfo=timezone.utc)          # DNUT, Thursday 10:11 ET


def frame(rows):
    return pd.DataFrame(rows, columns=["created_at", "symbols", "headline"])


def test_window_starts_at_previous_weekday_close():
    assert news.window_start(ALERT) == datetime(2026, 11, 4, 21, 0, tzinfo=timezone.utc)
    monday = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
    assert news.window_start(monday) == datetime(2026, 11, 6, 21, 0, tzinfo=timezone.utc)     # Friday 16:00 ET


def test_dnut_earnings_counts_and_only_before_the_alert():
    a = frame([
        ("2026-11-05T11:47:37Z", "DNUT", "Krispy Kreme Raises FY2026 Sales Guidance"),
        ("2026-11-05T15:23:36Z", "MSI,SEZL,U,TM,DNUT", "Big Stocks Moving Higher On Thursday"),    # after the alert
        ("2026-11-04T20:00:00Z", "DNUT", "yesterday before the close"),                         # before the window
    ])
    f = news.flags("DNUT", ALERT, a)
    assert f == {"news_any": True, "news_own": True, "news_earn": True, "news_n": 1,
                 "headline": "Krispy Kreme Raises FY2026 Sales Guidance"}


def test_roundups_and_other_symbols_are_not_company_news():
    a = frame([("2026-11-05T12:00:00Z", "A,B,DNUTX", "x"), ("2026-11-05T12:00:00Z", "A,B,DNUT", "Q3 results")])
    f = news.flags("DNUT", ALERT, a)
    assert f["news_any"] and not f["news_own"] and not f["news_earn"] and f["headline"] is None
    assert news.flags("DNUT", ALERT, frame([])) == {"news_any": False, "news_own": False, "news_earn": False,
                                                    "news_n": 0, "headline": None}


def test_non_earnings_company_news_is_own_but_not_earn():
    f = news.flags("XYZ", ALERT, frame([("2026-11-05T13:00:00Z", "XYZ", "XYZ announces FDA clearance")]))
    assert f["news_own"] and not f["news_earn"]


def test_untrusted_headline_and_symbol_are_bounded():
    assert news.clean("a\x00b\nc") == "a b c"
    long = news.clean("x" * 500)
    assert len(long) == news.HEADLINE_MAX and long.endswith("…")
    assert news.chart_url("DNUT") == "https://www.tradingview.com/chart/?symbol=DNUT"
    assert news.chart_url("DN UT&x=1") == ""
