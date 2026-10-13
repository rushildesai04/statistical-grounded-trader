"""Read-only Telegram collector for the alert channel(s).

The owner's Telethon user session reads channels it is subscribed to; this module
NEVER sends, edits, joins or deletes — the only Telegram operation used is
``iter_messages``. Telethon is imported lazily so the base install does not require
it (``pip install .[crowd]``); a missing dependency raises a clear error.

The Telethon client is reached through the ``MessageSource`` adapter protocol so
tests inject a fake. Messages are stored raw + strictly parsed and both writes are
idempotent (upsert on the natural keys), so re-running ``backfill``/``poll`` never
duplicates rows. Fetches are bounded (``max_messages_per_run``) and resume from the
max stored ``message_id`` per channel.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Protocol

from sqlalchemy import func, select

from sgt.config import get_settings
from sgt.core.secrets import get_secret
from sgt.crowd.models import CrowdAlert, TelegramMessage
from sgt.crowd.telegram_parser import (
    MAX_TEXT_LEN,
    ParsedAlert,
    ParsedNews,
    Unparsed,
    Validator,
    parse_message,
)
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert
from sgt.util import to_utc, utcnow

DEFAULT_MAX_MESSAGES_PER_RUN = 5_000


@dataclass(frozen=True, slots=True)
class SourceMessage:
    """Normalized raw message from any source adapter. ``date`` is UTC server time."""

    id: int
    date: datetime
    text: str


class MessageSource(Protocol):
    """Adapter over a Telegram message reader. Read-only by contract."""

    def iter_messages(
        self, channel: str, *, min_id: int, limit: int
    ) -> AsyncIterator[SourceMessage]:
        """Yield messages with ``id > min_id`` for ``channel``, at most ``limit``."""
        ...


class TelethonMessageSource:
    """Real adapter wrapping ``telethon.TelegramClient`` (read-only, lazy import)."""

    def __init__(self, session_path: str, api_id: int, api_hash: str):
        self._session_path = session_path
        self._api_id = api_id
        self._api_hash = api_hash

    def _new_client(self):
        try:
            from telethon import TelegramClient
        except ImportError as exc:  # optional dependency
            raise RuntimeError(
                "telethon is not installed; install the crowd extra: pip install .[crowd]"
            ) from exc
        return TelegramClient(self._session_path, self._api_id, self._api_hash)

    async def iter_messages(
        self, channel: str, *, min_id: int, limit: int
    ) -> AsyncIterator[SourceMessage]:
        client = self._new_client()
        async with client:  # connects; uses the existing user session file
            async for msg in client.iter_messages(channel, min_id=min_id, limit=limit):
                if msg is None:
                    continue
                yield SourceMessage(
                    id=int(msg.id),
                    date=to_utc(msg.date),
                    text=msg.message or "",
                )


class TelegramCollector:
    """Fetch, store and parse Telegram alert messages idempotently."""

    name = "telegram"

    def __init__(
        self,
        channels: list[str],
        session_path: str,
        *,
        source: MessageSource | None = None,
        api_id: int | None = None,
        api_hash: str | None = None,
        validator: Validator | None = None,
        max_messages_per_run: int = DEFAULT_MAX_MESSAGES_PER_RUN,
    ):
        if max_messages_per_run <= 0:
            raise ValueError("max_messages_per_run must be > 0")
        self.channels = list(channels)
        self.session_path = session_path
        self.validator = validator
        self.max_messages_per_run = max_messages_per_run
        self._source = source
        self._api_id = api_id
        self._api_hash = api_hash

    # ----------------------------------------------------------------- source

    def _get_source(self) -> MessageSource:
        if self._source is not None:
            return self._source
        api_id = self._api_id
        api_hash = self._api_hash
        if api_id is None:
            api_id = int(get_secret("telegram_api_id"))
        if api_hash is None:
            api_hash = get_secret("telegram_api_hash")
        self._source = TelethonMessageSource(self.session_path, int(api_id), str(api_hash))
        return self._source

    # ----------------------------------------------------------------- public

    async def backfill(self, since: datetime) -> int:
        """Fetch messages newer than the max stored id per channel, keeping only
        those posted at or after ``since`` (tz-aware UTC). Returns rows stored."""
        if since.tzinfo is None:
            raise ValueError("since must be tz-aware UTC")
        return await self._fetch(to_utc(since))

    async def poll(self) -> int:
        """Fetch all messages newer than the max stored id per channel."""
        return await self._fetch(None)

    # ----------------------------------------------------------------- internal

    def _max_stored_id(self, session, channel: str) -> int:
        stmt = select(func.max(TelegramMessage.message_id)).where(
            TelegramMessage.channel == channel
        )
        return int(session.execute(stmt).scalar() or 0)

    async def _fetch(self, since: datetime | None) -> int:
        source = self._get_source()
        stored = 0
        with get_session() as session:
            for channel in self.channels:
                min_id = self._max_stored_id(session, channel)
                seen = 0
                async for msg in source.iter_messages(
                    channel, min_id=min_id, limit=self.max_messages_per_run
                ):
                    if seen >= self.max_messages_per_run:
                        break
                    seen += 1
                    if msg.id <= min_id:
                        continue  # defensive: adapter must honor min_id
                    posted_at = to_utc(msg.date)
                    if since is not None and posted_at < since:
                        continue
                    self._store(session, channel, msg, posted_at)
                    stored += 1
        return stored

    def _store(self, session, channel: str, msg: SourceMessage, posted_at: datetime) -> None:
        text = (msg.text or "")[:MAX_TEXT_LEN]
        items = parse_message(text, posted_at, channel, msg.id, validator=self.validator)
        parsed = [it for it in items if not isinstance(it, Unparsed)]
        status = "parsed" if parsed else "unparsed"

        _upsert(
            session,
            TelegramMessage,
            {
                "channel": channel,
                "message_id": msg.id,
                "posted_at": posted_at,
                "raw_text": text,
                "ingested_at": utcnow(),
                "parse_status": status,
            },
            ["channel", "message_id"],
        )

        for it in parsed:
            if isinstance(it, ParsedAlert):
                row = {
                    "channel": channel,
                    "message_id": msg.id,
                    "idx": it.idx,
                    "symbol": it.symbol,
                    "alert_type": it.alert_type,
                    "price": it.price,
                    "posted_at": posted_at,
                    "has_news": None,
                    "headline": None,
                }
            elif isinstance(it, ParsedNews):
                row = {
                    "channel": channel,
                    "message_id": msg.id,
                    "idx": it.idx,
                    "symbol": it.symbol,
                    "alert_type": "news",
                    "price": None,
                    "posted_at": posted_at,
                    "has_news": it.has_news,
                    "headline": it.headline or None,
                }
            else:  # pragma: no cover - parsed excludes Unparsed
                continue
            _upsert(session, CrowdAlert, row, ["channel", "message_id", "idx"])
