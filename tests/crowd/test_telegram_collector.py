"""Collector idempotency and min_id resume against a fake MessageSource."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import func, select

from sgt.crowd.models import CrowdAlert, TelegramMessage
from sgt.crowd.telegram_collector import SourceMessage, TelegramCollector
from sgt.db.session import get_session


class FakeSource:
    """In-memory, read-only message source mimicking telethon's min_id/limit."""

    def __init__(self, by_channel: dict[str, list[SourceMessage]]):
        self.by_channel = by_channel
        self.calls: list[tuple[str, int, int]] = []

    async def iter_messages(
        self, channel: str, *, min_id: int, limit: int
    ) -> AsyncIterator[SourceMessage]:
        self.calls.append((channel, min_id, limit))
        msgs = sorted(self.by_channel.get(channel, []), key=lambda m: m.id, reverse=True)
        yielded = 0
        for m in msgs:
            if m.id <= min_id:
                continue
            if yielded >= limit:
                break
            yielded += 1
            yield m


def _msg(i: int, text: str, minute: int = 0) -> SourceMessage:
    return SourceMessage(id=i, date=datetime(2026, 10, 9, 18, minute, tzinfo=timezone.utc), text=text)


def _counts():
    with get_session() as session:
        msgs = session.execute(select(func.count()).select_from(TelegramMessage)).scalar_one()
        alerts = session.execute(select(func.count()).select_from(CrowdAlert)).scalar_one()
    return int(msgs), int(alerts)


async def test_poll_stores_and_parses():
    src = FakeSource({
        "chan": [
            _msg(1, "YMAT new high 4.49 💰", 0),
            _msg(2, "WFF  gaining volume  13.56", 1),
            _msg(3, "Welcome NEW MEMBERS, NOTE : nothing here", 2),
        ]
    })
    collector = TelegramCollector(["chan"], "unused.session", source=src)
    stored = await collector.poll()
    assert stored == 3
    msgs, alerts = _counts()
    assert msgs == 3
    assert alerts == 2  # the welcome message yields no crowd_alerts

    with get_session() as session:
        status = session.execute(
            select(TelegramMessage.parse_status).where(TelegramMessage.message_id == 3)
        ).scalar_one()
    assert status == "unparsed"


async def test_poll_idempotent_and_min_id_resume():
    src = FakeSource({
        "chan": [
            _msg(1, "YMAT new high 4.49 💰", 0),
            _msg(2, "WFF  gaining volume  13.56", 1),
        ]
    })
    collector = TelegramCollector(["chan"], "unused.session", source=src)

    first = await collector.poll()
    assert first == 2
    # Re-run: min_id now 2, nothing new -> no duplicates.
    second = await collector.poll()
    assert second == 0
    assert _counts() == (2, 2)
    # Second call must have resumed from max stored id.
    assert src.calls[-1][1] == 2

    # A new message arrives; only it is fetched and stored.
    src.by_channel["chan"].append(_msg(3, "NCPL  gaining momentum  1.53", 2))
    third = await collector.poll()
    assert third == 1
    assert _counts() == (3, 3)


async def test_backfill_filters_by_since():
    src = FakeSource({
        "chan": [
            SourceMessage(1, datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc), "YMAT new high 1.00"),
            SourceMessage(2, datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc), "WFF new high 2.00"),
        ]
    })
    collector = TelegramCollector(["chan"], "unused.session", source=src)
    stored = await collector.backfill(datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert stored == 1
    msgs, alerts = _counts()
    assert msgs == 1 and alerts == 1


async def test_reparse_updates_alert_rows_without_duplicating():
    src = FakeSource({"chan": [_msg(1, "YMAT new high 4.49 💰", 0)]})
    collector = TelegramCollector(["chan"], "unused.session", source=src)
    await collector.poll()
    # Force a re-store of the same id by clearing the stored-id high-water mark path:
    # calling _store directly simulates a re-fetch of the same message.
    with get_session() as session:
        collector._store(session, "chan", _msg(1, "YMAT new high 4.49 💰", 0),
                         datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc))
    assert _counts() == (1, 1)
