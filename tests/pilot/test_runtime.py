from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import sgt.brokers.schwab as schwab_mod
import sgt.core.secrets as secrets_mod
from sgt.core.interfaces import AdapterHealth
from sgt.ops.store import OpsStore
from sgt.pilot import runtime

T0 = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


class Broker:
    fail = False
    expires: datetime | None = T0 + timedelta(days=5)

    def __init__(self, account_id, last4):
        self.account_id, self.last4, self.closed = account_id, last4, False

    async def connect(self):
        if Broker.fail:
            raise RuntimeError("401")

    async def health(self):
        return AdapterHealth(ok=True, detail="", auth_expires_at=Broker.expires)

    async def close(self):
        self.closed = True


@pytest.fixture
def h(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(schwab_mod, "SchwabBroker", Broker)
    monkeypatch.setattr(secrets_mod, "get_secret", lambda name, required=True: "1234")
    Broker.fail, Broker.expires = False, T0 + timedelta(days=5)
    texts: list[str] = []
    handle = runtime.PilotHandle(OpsStore(tmp_path / "ops.db"), texts.append)
    return handle, texts


async def test_off_unless_enabled(h, monkeypatch):
    handle, _ = h
    monkeypatch.delenv(runtime.ENV, raising=False)
    assert await handle.get(T0) is None


async def test_connect_failure_texts_once_and_retries(h, monkeypatch):
    handle, texts = h
    monkeypatch.setenv(runtime.ENV, "1")
    Broker.fail = True
    assert await handle.get(T0) is None
    assert await handle.get(T0 + timedelta(minutes=5)) is None      # no retry inside 10 min
    Broker.fail = False
    assert await handle.get(T0 + timedelta(minutes=6)) is None
    p = await handle.get(T0 + timedelta(minutes=11))
    assert p is not None and p.broker.last4 == "1234"
    assert len(texts) == 1 and "1234" not in texts[0]                # account digits never texted


async def test_login_reminder_once_per_day(h, monkeypatch):
    handle, texts = h
    monkeypatch.setenv(runtime.ENV, "1")
    await handle.get(T0)
    await handle.login_check(T0)
    assert texts == []
    Broker.expires = T0 + timedelta(hours=20)
    await handle.login_check(T0)
    await handle.login_check(T0 + timedelta(hours=1))
    assert len(texts) == 1 and "within 20 h" in unicodedata.normalize("NFKC", texts[0])
