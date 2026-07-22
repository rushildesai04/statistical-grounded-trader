"""Turn raw source rows (Reddit posts/comments, StockTwits, news) into scored
`mentions`. This is where ticker extraction + sentiment are applied once per
text span and persisted, so downstream feature building is a pure aggregation.
"""
from __future__ import annotations

import math

from sgt.db.models import (
    Mention, NewsArticle, RedditComment, RedditPost, StockTwitsMessage,
)
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert
from sgt.ingestion.universe import load_universe_set
from sgt.processing.sentiment import SentimentScorer, get_scorer
from sgt.processing.ticker_extraction import extract_tickers


def build_mentions(scorer: SentimentScorer | None = None, batch: int = 256) -> int:
    """Scan raw sources, extract validated tickers, score sentiment, upsert
    mentions. Idempotent: re-running rescoring the same spans overwrites rows."""
    universe = load_universe_set()
    scorer = scorer or get_scorer()
    n = 0
    with get_session() as session:
        n += _process_reddit_posts(session, universe, scorer)
        n += _process_reddit_comments(session, universe, scorer)
        n += _process_stocktwits(session, universe, scorer)
        n += _process_news(session, universe, scorer)
    return n


def _emit(session, scorer, records: list[dict]) -> int:
    """records: {source_type, source_id, text, created_utc, author_hash, weight}."""
    if not records:
        return 0
    scored = scorer.score([r["text"] for r in records])
    universe_hits = 0
    for rec, sent in zip(records, scored):
        for ticker in rec["tickers"]:
            _upsert(session, Mention, {
                "source_type": rec["source_type"],
                "source_id": rec["source_id"],
                "ticker": ticker,
                "created_utc": rec["created_utc"],
                "author_hash": rec.get("author_hash", ""),
                "weight": rec.get("weight", 1.0),
                "sentiment_label": sent["label"],
                "sentiment_scores": sent["scores"],
                "model_version": scorer.model_version,
            }, ["source_type", "source_id", "ticker"])
            universe_hits += 1
    return universe_hits


def _weight(score: int) -> float:
    return 1.0 + math.log1p(max(0, score))


def _process_reddit_posts(session, universe, scorer) -> int:
    n = 0
    for p in session.query(RedditPost).all():
        text = f"{p.title} {p.body}"
        tickers = extract_tickers(text, universe)
        if not tickers:
            continue
        n += _emit(session, scorer, [{
            "source_type": "reddit_post", "source_id": p.reddit_id, "text": text,
            "tickers": tickers, "created_utc": p.created_utc,
            "author_hash": p.author_hash, "weight": _weight(p.score),
        }])
    return n


def _process_reddit_comments(session, universe, scorer) -> int:
    n = 0
    for c in session.query(RedditComment).all():
        tickers = extract_tickers(c.body, universe)
        if not tickers:
            continue
        n += _emit(session, scorer, [{
            "source_type": "reddit_comment", "source_id": c.reddit_id, "text": c.body,
            "tickers": tickers, "created_utc": c.created_utc,
            "author_hash": c.author_hash, "weight": _weight(c.score),
        }])
    return n


def _process_stocktwits(session, universe, scorer) -> int:
    n = 0
    for m in session.query(StockTwitsMessage).all():
        # symbol is authoritative; still validate against universe
        tickers = {m.symbol.upper()} & universe
        tickers |= extract_tickers(m.body, universe)
        if not tickers:
            continue
        n += _emit(session, scorer, [{
            "source_type": "stocktwits", "source_id": m.message_id, "text": m.body,
            "tickers": tickers, "created_utc": m.created_utc,
            "author_hash": "", "weight": 1.0 + math.log1p(max(0, m.likes)),
        }])
    return n


def _process_news(session, universe, scorer) -> int:
    n = 0
    for a in session.query(NewsArticle).all():
        tickers = {a.ticker.upper()} & universe
        tickers |= extract_tickers(a.title, universe)
        if not tickers:
            continue
        n += _emit(session, scorer, [{
            "source_type": "news", "source_id": a.article_id, "text": a.title,
            "tickers": tickers, "created_utc": a.published_utc,
            "author_hash": "", "weight": 1.0,
        }])
    return n
