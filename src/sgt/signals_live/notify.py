"""Notifications and owner commands: Twilio SMS and/or Telegram Saved Messages.

Commands (from the owner only): status, pause, resume, kill (flatten + pause), help.
Twilio inbound SMS is POLLED from the Messages API (no public webhook needed); only
messages from the configured owner number are accepted. Secrets (keychain service sgt):
``twilio_account_sid``, ``twilio_auth_token``, ``twilio_from_number``, ``owner_phone``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Awaitable, Callable, Protocol

import httpx

from sgt.core.secrets import get_secret

log = logging.getLogger(__name__)
COMMANDS = frozenset({"status", "pause", "resume", "kill", "help"})
SMS_MAX = 1500
TIMEOUT_S = 10.0


def parse_command(text: str) -> str | None:
    word = (text or "").strip().lower().lstrip("/")
    return word if word in COMMANDS else None


class Notifier(Protocol):
    important_only: bool

    async def send(self, text: str) -> None: ...


class TelegramSelfNotifier:
    """Posts to the logged-in account's own Saved Messages (never to anyone else)."""

    important_only = False

    def __init__(self, client) -> None:
        self._client = client

    async def send(self, text: str) -> None:
        await self._client.send_message("me", text[:4000])


class TwilioSms:
    important_only = True  # entries, exits, stops, summaries; skips stay on Telegram

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.sid = get_secret("twilio_account_sid")
        self.from_number = get_secret("twilio_from_number")
        self.owner = get_secret("owner_phone")
        token = get_secret("twilio_auth_token")
        self._c = client or httpx.AsyncClient(
            base_url=f"https://api.twilio.com/2010-04-01/Accounts/{self.sid}", auth=(self.sid, token),
            timeout=TIMEOUT_S)
        self._seen: set[str] = set()
        self._since = datetime.now(timezone.utc)

    async def send(self, text: str) -> None:
        r = await self._c.post("/Messages.json", data={"From": self.from_number, "To": self.owner,
                                                       "Body": text[:SMS_MAX]})
        if r.status_code >= 400:
            raise RuntimeError(f"twilio send HTTP {r.status_code}: {r.text[:200]}")

    async def poll_commands(self) -> list[str]:
        """Owner commands received since start (each returned once)."""
        r = await self._c.get("/Messages.json", params={"To": self.from_number, "From": self.owner,
                                                        "PageSize": 20})
        if r.status_code >= 400:
            raise RuntimeError(f"twilio poll HTTP {r.status_code}")
        out = []
        for m in reversed(r.json().get("messages", [])):
            if m["sid"] in self._seen or m.get("direction") != "inbound" or m.get("from") != self.owner:
                continue
            self._seen.add(m["sid"])
            sent = m.get("date_sent") or m.get("date_created")
            if sent and parsedate_to_datetime(sent) < self._since:
                continue
            if (cmd := parse_command(m.get("body", ""))) is not None:
                out.append(cmd)
        return out


class Fanout:
    """Send to every channel; one failing channel never blocks the others."""

    def __init__(self, *notifiers: Notifier) -> None:
        self._n = [n for n in notifiers if n is not None]

    async def send(self, text: str, important: bool = True) -> None:
        for n in self._n:
            if n.important_only and not important:
                continue
            try:
                await n.send(text)
            except Exception as exc:  # noqa: BLE001 - logged; other channels still deliver
                log.error("notify via %s failed: %s", type(n).__name__, exc)


CommandHandler = Callable[[str], Awaitable[str]]
