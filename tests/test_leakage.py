"""The load-bearing test. If a feature row can see data at or after its
as_of_ts, the whole platform's results are invalid. This asserts the firewall
in features.store._social_features directly, then end-to-end.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sgt.features.store import _social_features

AS_OF = datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)


def _m(hours_before: float, signed: float = 0.0, author: str = "a", weight: float = 1.0):
    return {"created_utc": AS_OF - timedelta(hours=hours_before),
            "signed": signed, "author_hash": author, "weight": weight}


def test_mention_exactly_at_as_of_is_excluded():
    # created_utc == as_of must NOT count (strict <).
    m = {"created_utc": AS_OF, "signed": 1.0, "author_hash": "x", "weight": 1.0}
    feats = _social_features([m], AS_OF)
    assert feats["mention_count_24h"] == 0.0


def test_future_mention_is_excluded():
    m = {"created_utc": AS_OF + timedelta(hours=1), "signed": 1.0,
         "author_hash": "x", "weight": 1.0}
    feats = _social_features([m], AS_OF)
    assert feats["mention_count_24h"] == 0.0
    assert feats["mention_count_6h"] == 0.0


def test_only_trailing_window_counts():
    mentions = [
        _m(1),    # within 6h and 24h
        _m(5),    # within 6h and 24h
        _m(10),   # within 24h only
        _m(30),   # in prior-24h window, not current
        _m(100),  # outside all windows
    ]
    feats = _social_features(mentions, AS_OF)
    assert feats["mention_count_24h"] == 3.0
    assert feats["mention_count_6h"] == 2.0
    assert feats["mention_delta_24h"] == 3.0 - 1.0  # current(3) - prior(1)


def test_unique_authors():
    mentions = [_m(1, author="a"), _m(2, author="a"), _m(3, author="b")]
    feats = _social_features(mentions, AS_OF)
    assert feats["unique_authors_24h"] == 2.0
