"""News features: point-in-time cutoff, recap exclusion, 24h window, category of latest."""
from __future__ import annotations

import pandas as pd

from sgt.decision.news_features import NewsIndex, classify


def _idx():
    rows = [("2026-01-05T14:00:00Z", "ABC", "ABC Receives FDA Approval For Drug X"),
            ("2026-01-05T14:00:40Z", "ABC", "Why ABC Shares Are Trading Higher"),            # recap
            ("2026-01-05T14:00:58Z", "ABC", "ABC Announces $5M Registered Direct Offering"),  # too late
            ("2026-01-04T10:00:00Z", "ABC", "ABC Enters Partnership With Big Co"),
            ("2026-01-05T13:00:00Z", "ABC,XYZ", "ABC And XYZ Merge")]                        # multi
    return NewsIndex.from_frame(pd.DataFrame(rows, columns=["created_at", "symbols", "headline"]),
                                latency_s=5.0)


def _ns(s):
    return pd.Timestamp(s).value


def test_cutoff_and_latest_category():
    f = _idx().features("abc", _ns("2026-01-05T14:00:00Z"))       # bar 14:00, closes 14:01
    assert f["f_news_last_fda"] == 1.0 and f["f_news_offering_24h"] == 0.0   # offering at :58 > :55
    assert abs(f["f_news_mins_since"] - 55 / 60) < 1e-9
    assert f["f_news_count_24h"] == 1.0                              # partnership was >24h before


def test_no_news_and_prior_bar():
    idx = _idx()
    assert idx.features("ABC", _ns("2026-01-05T13:58:00Z"))["f_news_last_fda"] == 0.0
    assert idx.features("ZZZ", _ns("2026-01-05T14:00:00Z"))["f_news_mins_since"] == 1440.0


def test_classify_excludes_recaps_and_analyst_notes():
    assert classify("Why ABC Shares Are Trading Higher") is None
    assert classify("B of A Maintains Buy On ABC, Raises Price Target") is None
    assert classify("ABC Announces Topline Phase 2 Results") == "fda"
    assert classify("ABC Prices $10M Offering") == "offering"
