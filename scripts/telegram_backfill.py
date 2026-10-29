"""Backfill Telegram alert-channel history into the DB (read-only).

Usage:
    python scripts/telegram_backfill.py --since 2026-09-01
    python scripts/telegram_backfill.py --since 2026-09-01T00:00:00 --channels chanA,chanB

API id/hash come from the secret store (``telegram_api_id`` / ``telegram_api_hash``);
the user session file path comes from settings (``SGT_TELEGRAM_SESSION_PATH``).
Channels default to ``SGT_TELEGRAM_CHANNELS`` when ``--channels`` is omitted.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from sgt.config import get_settings
from sgt.crowd.telegram_collector import TelegramCollector
from sgt.db.session import init_db


def _parse_since(value: str) -> datetime:
    text = value.strip()
    if len(text) == 10:  # YYYY-MM-DD
        dt = datetime.strptime(text, "%Y-%m-%d")
    else:
        dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Telegram alert history.")
    parser.add_argument("--since", required=True, help="ISO date/datetime, e.g. 2026-09-01")
    parser.add_argument("--channels", default="", help="comma-separated channels (overrides config)")
    parser.add_argument("--max-messages", type=int, default=20_000, help="per-channel cap per batch")
    parser.add_argument("--max-batches", type=int, default=50, help="stop after this many batches")
    args = parser.parse_args()

    settings = get_settings()
    channels = (
        [c.strip() for c in args.channels.split(",") if c.strip()]
        if args.channels
        else settings.telegram_channel_list
    )
    if not channels:
        raise SystemExit("no channels: pass --channels or set SGT_TELEGRAM_CHANNELS")

    since = _parse_since(args.since)
    init_db()
    collector = TelegramCollector(
        channels,
        settings.telegram_session_path,
        max_messages_per_run=args.max_messages,
    )
    # Oldest-first batches resume from the max stored id; loop until caught up.
    total = 0
    for batch in range(1, args.max_batches + 1):
        stored = asyncio.run(collector.backfill(since))
        total += stored
        print(f"batch {batch}: stored {stored}")
        if stored == 0:
            break
    else:
        print(f"WARNING: hit --max-batches={args.max_batches}; rerun to continue")
    print(f"Telegram backfill: stored {total} messages since {since.isoformat()}")


if __name__ == "__main__":
    main()
