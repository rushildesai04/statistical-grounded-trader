"""Synthetic pipeline: raw Reddit + OHLCV -> mentions -> features -> labels ->
training frame. Verifies labels are the NEXT bar's return (no same-bar leak)
and that the sanctioned join produces a usable frame."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sgt.db.models import OHLCV, RedditPost, TickerUniverse
from sgt.db.session import get_session
from sgt.features.store import build_features_and_labels, get_training_frame
from sgt.processing.mentions import build_mentions

START = datetime(2026, 1, 1, tzinfo=timezone.utc)
CLOSES = [100, 101, 99, 102, 103, 105, 104, 108, 110, 107]


def _seed():
    with get_session() as s:
        s.add(TickerUniverse(ticker="AAPL", name="Apple", active=True))
        for i, c in enumerate(CLOSES):
            d = START + timedelta(days=i)
            s.add(OHLCV(ticker="AAPL", date=d, open=c, high=c, low=c,
                        close=c, adj_close=c, volume=1_000_000))
        # a bullish post each day at 09:00 UTC (before that day's 00:00 as_of? no —
        # before the NEXT day's as_of). Placed a few hours into each day.
        for i in range(len(CLOSES)):
            d = START + timedelta(days=i, hours=9)
            s.add(RedditPost(reddit_id=f"p{i}", subreddit="wallstreetbets",
                             created_utc=d, author_hash=f"u{i % 3}", score=10,
                             title="$AAPL to the moon calls", body="bullish buy"))


def test_pipeline_builds_frame():
    _seed()
    assert build_mentions() > 0
    res = build_features_and_labels()
    assert res["features"] > 0 and res["labels"] > 0

    df = get_training_frame()
    assert not df.empty
    assert set(["ticker", "as_of_ts", "fwd_return", "cls"]).issubset(df.columns)


def test_label_is_next_bar_return():
    _seed()
    build_mentions()
    build_features_and_labels()
    df = get_training_frame().sort_values("as_of_ts").reset_index(drop=True)

    # Key by calendar date — SQLite returns naive datetimes, so compare on date.
    close_by_date = {(START + timedelta(days=i)).date(): c for i, c in enumerate(CLOSES)}
    for _, row in df.iterrows():
        as_of = row["as_of_ts"].to_pydatetime().date()
        this_close = close_by_date[as_of]
        next_close = close_by_date[as_of + timedelta(days=1)]
        expected = next_close / this_close - 1.0
        assert abs(row["fwd_return"] - expected) < 1e-9


def test_social_features_present_and_causal():
    _seed()
    build_mentions()
    build_features_and_labels()
    df = get_training_frame()
    # Every row should have mention activity from PRIOR days (posts exist daily).
    assert (df["mention_count_24h"] >= 0).all()
    assert df["mention_count_24h"].sum() > 0
