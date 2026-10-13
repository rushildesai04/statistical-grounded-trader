"""ORM tables owned by sgt.crowd. Registered on the shared Base via sgt.db.registry.

All timestamps are timezone-aware UTC. ``telegram_messages`` stores every fetched
message raw (bounded) for audit/replay; ``crowd_alerts`` stores the strictly parsed
items, one row per item index within a message so re-parsing a message is idempotent
on (channel, message_id, idx).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sgt.db.models import Base


class TelegramMessage(Base):
    """One fetched Telegram message, stored raw for audit/replay.

    Primary key (channel, message_id) is Telegram's own unique pair, so re-fetching
    the same message upserts in place. ``posted_at`` is Telegram server time (UTC).
    ``parse_status`` is "parsed" when at least one alert/news item was extracted,
    "unparsed" otherwise.
    """

    __tablename__ = "telegram_messages"

    channel: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    parse_status: Mapped[str] = mapped_column(String(16), default="unparsed", index=True)


class CrowdAlert(Base):
    """One strictly parsed alert or news item from a Telegram message.

    (channel, message_id, idx) is unique: ``idx`` is the item's position in the
    parse output, so a message with several alert lines produces several rows and
    re-parsing never duplicates them.
    """

    __tablename__ = "crowd_alerts"

    channel: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    alert_type: Mapped[str] = mapped_column(String(32), index=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    has_news: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    headline: Mapped[str | None] = mapped_column(Text, nullable=True)
