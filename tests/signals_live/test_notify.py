"""Notification fan-out and owner-only SMS commands."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx

from sgt.signals_live import notify as nmod
from sgt.signals_live.notify import Fanout, TwilioSms, parse_command


class Rec:
    def __init__(self, important_only, fail=False):
        self.important_only, self.fail, self.got = important_only, fail, []

    async def send(self, text):
        if self.fail:
            raise RuntimeError("down")
        self.got.append(text)


async def test_fanout_routes_by_importance_and_isolates_failures():
    tg, sms, broken = Rec(False), Rec(True), Rec(False, fail=True)
    f = Fanout(broken, tg, None, sms)
    await f.send("skip X", important=False)
    await f.send("BOUGHT X")
    assert tg.got == ["skip X", "BOUGHT X"] and sms.got == ["BOUGHT X"]


def test_parse_command():
    assert parse_command(" Kill ") == "kill" and parse_command("/status") == "status"
    assert parse_command("kill everything now") is None and parse_command("") is None


async def test_twilio_poll_owner_only_and_after_start(monkeypatch):
    secrets = {"twilio_account_sid": "AC1", "twilio_auth_token": "t", "twilio_from_number": "+15550001",
               "owner_phone": "+15550002"}
    monkeypatch.setattr(nmod, "get_secret", lambda n, **k: secrets[n])
    now = datetime.now(timezone.utc)
    msgs = [
        {"sid": "1", "direction": "inbound", "from": "+15550002", "body": "kill", "date_sent": format_datetime(now + timedelta(seconds=5))},
        {"sid": "2", "direction": "inbound", "from": "+15559999", "body": "kill", "date_sent": format_datetime(now + timedelta(seconds=5))},
        {"sid": "3", "direction": "inbound", "from": "+15550002", "body": "status", "date_sent": format_datetime(now - timedelta(hours=1))},
        {"sid": "4", "direction": "outbound-api", "from": "+15550002", "body": "pause", "date_sent": format_datetime(now + timedelta(seconds=5))},
    ]
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"messages": msgs}))
    sms = TwilioSms(client=httpx.AsyncClient(transport=transport, base_url="https://x"))
    assert await sms.poll_commands() == ["kill"]
    assert await sms.poll_commands() == []  # each command once
