"""SQLAlchemy ORM models. All timestamps are timezone-aware UTC.

Sources feed a unified `mentions` table via (source_type, source_id) so any
social/news source produces sentiment rows through the same pipeline.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), index=True)


class RedditPost(Base):
    __tablename__ = "reddit_posts"
    reddit_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    subreddit: Mapped[str] = mapped_column(String(64), index=True)
    created_utc: Mapped[datetime] = _ts()
    author_hash: Mapped[str] = mapped_column(String(64))
    score: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    permalink: Mapped[str] = mapped_column(Text, default="")


class RedditComment(Base):
    __tablename__ = "reddit_comments"
    reddit_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    post_id: Mapped[str] = mapped_column(String(20), ForeignKey("reddit_posts.reddit_id"), index=True)
    subreddit: Mapped[str] = mapped_column(String(64), index=True)
    created_utc: Mapped[datetime] = _ts()
    author_hash: Mapped[str] = mapped_column(String(64))
    score: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[str] = mapped_column(Text, default="")


class StockTwitsMessage(Base):
    __tablename__ = "stocktwits_messages"
    message_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    created_utc: Mapped[datetime] = _ts()
    body: Mapped[str] = mapped_column(Text, default="")
    # StockTwits-native label when the author tagged the message: Bullish/Bearish/None
    native_sentiment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    likes: Mapped[int] = mapped_column(Integer, default=0)


class NewsArticle(Base):
    __tablename__ = "news_articles"
    article_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    published_utc: Mapped[datetime] = _ts()
    title: Mapped[str] = mapped_column(Text, default="")
    publisher: Mapped[str] = mapped_column(String(128), default="")
    link: Mapped[str] = mapped_column(Text, default="")


class OHLCV(Base):
    __tablename__ = "ohlcv"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    adj_close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)
    provider: Mapped[str] = mapped_column(String(32), default="yfinance")


class TickerUniverse(Base):
    __tablename__ = "ticker_universe"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    name: Mapped[str] = mapped_column(Text, default="")
    exchange: Mapped[str] = mapped_column(String(32), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class Mention(Base):
    """One row per (source text span, ticker). Sentiment attached here."""
    __tablename__ = "mentions"
    source_type: Mapped[str] = mapped_column(String(24), primary_key=True)  # reddit_post|reddit_comment|stocktwits|news
    source_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    created_utc: Mapped[datetime] = _ts()
    author_hash: Mapped[str] = mapped_column(String(64), default="")
    weight: Mapped[float] = mapped_column(Float, default=1.0)  # e.g. log1p(score)
    sentiment_label: Mapped[str] = mapped_column(String(16), default="neutral")
    sentiment_scores: Mapped[dict] = mapped_column(JSON, default=dict)  # {pos,neg,neu}
    model_version: Mapped[str] = mapped_column(String(64), default="")


class Feature(Base):
    """Point-in-time feature row. Never updated in place. Only data with
    timestamp < as_of_ts may contribute (enforced in features/store.py)."""
    __tablename__ = "features"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    as_of_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    feature_set_version: Mapped[str] = mapped_column(String(32), default="v1")

    mention_count_24h: Mapped[float] = mapped_column(Float, default=0.0)
    mention_count_6h: Mapped[float] = mapped_column(Float, default=0.0)
    unique_authors_24h: Mapped[float] = mapped_column(Float, default=0.0)
    weighted_mentions_24h: Mapped[float] = mapped_column(Float, default=0.0)
    mean_sentiment_24h: Mapped[float] = mapped_column(Float, default=0.0)
    net_sentiment_24h: Mapped[float] = mapped_column(Float, default=0.0)
    sentiment_dispersion_24h: Mapped[float] = mapped_column(Float, default=0.0)
    mention_delta_24h: Mapped[float] = mapped_column(Float, default=0.0)  # 24h vs prior 24h
    ret_1d: Mapped[float] = mapped_column(Float, default=0.0)
    ret_5d: Mapped[float] = mapped_column(Float, default=0.0)
    vol_5d: Mapped[float] = mapped_column(Float, default=0.0)


class Label(Base):
    __tablename__ = "labels"
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    as_of_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    fwd_return: Mapped[float] = mapped_column(Float, default=0.0)
    cls: Mapped[str] = mapped_column(String(8), default="flat")  # up|down|flat
    vol_bucket: Mapped[str] = mapped_column(String(8), default="mid")  # low|mid|high


class ModelRegistry(Base):
    __tablename__ = "model_registry"
    model_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), default="")
    feature_set_version: Mapped[str] = mapped_column(String(32), default="v1")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_utc: Mapped[datetime] = _ts()
