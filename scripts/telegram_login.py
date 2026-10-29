"""One-time interactive Telegram login + find the alert channel (read-only).

Run in your own terminal (it prompts for phone, the login code and 2FA password):
    .venv/bin/python scripts/telegram_login.py
    .venv/bin/python scripts/telegram_login.py --title "NEWS, VOLUME, BREAKOUTS"

Creates the user session file at settings ``SGT_TELEGRAM_SESSION_PATH`` (mode 600),
then lists subscribed chats whose title matches ``--title`` with the value to pass
to ``telegram_backfill.py --channels``. Never sends messages or joins anything.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
from pathlib import Path

from telethon import TelegramClient

from sgt.config import get_settings
from sgt.core.secrets import get_secret


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


async def _run(title: str) -> None:
    session_path = get_settings().telegram_session_path
    Path(session_path).parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(session_path, int(get_secret("telegram_api_id")), get_secret("telegram_api_hash"))
    async with client:  # start(): prompts for phone / code / password on first run
        me = await client.get_me()
        print(f"logged in (user id {me.id})")
        want = _norm(title)
        found = 0
        async for dialog in client.iter_dialogs():
            if want not in _norm(dialog.name or ""):
                continue
            found += 1
            username = getattr(dialog.entity, "username", None)
            total = (await client.get_messages(dialog.entity, limit=0)).total
            print(f"MATCH title={dialog.name!r} id={dialog.id} username={username!r} messages={total}")
            print(f"  use: --channels {username or dialog.id}")
        if not found:
            print(f"no subscribed chat title contains {title!r}; join the channel in the app first")
    sess = session_path if session_path.endswith(".session") else session_path + ".session"
    if os.path.exists(sess):
        os.chmod(sess, 0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram one-time login and channel lookup.")
    parser.add_argument("--title", default="NEWS, VOLUME, BREAKOUTS")
    asyncio.run(_run(parser.parse_args().title))


if __name__ == "__main__":
    main()
