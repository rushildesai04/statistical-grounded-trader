"""The outside probe for a machine that is down: off until an address is set, carries no data, never raises."""
from __future__ import annotations

from pathlib import Path

from sgt.ops import deadman


def test_without_an_address_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("SGT_SECRET_DEADMAN_URL", raising=False)
    monkeypatch.setattr("sgt.core.secrets.get_secret", lambda name, required=True: None)
    calls = []
    assert deadman.address() is None and deadman.ping(get=lambda *a, **k: calls.append(a)) == "off" and calls == []


def test_one_plain_call_a_minute_and_a_fail_call_when_the_listener_is_silent(monkeypatch):
    monkeypatch.setattr("sgt.core.secrets.get_secret", lambda name, required=True: "https://probe.example/ping/abc/ ")
    calls = []
    get = lambda url, timeout: calls.append((url, timeout))  # noqa: E731
    assert deadman.ping(get=get) == "sent" and deadman.ping(ok=False, get=get) == "sent"
    assert calls == [("https://probe.example/ping/abc", 5.0), ("https://probe.example/ping/abc/fail", 5.0)]   # no data


def test_a_bad_address_or_a_failed_call_never_raises(monkeypatch):
    for bad in ("http://plain.example/x", "ftp://x", "https://a b.example", "https://" + "x" * 400):
        monkeypatch.setattr("sgt.core.secrets.get_secret", lambda name, required=True, v=bad: v)
        assert deadman.address() is None and deadman.ping(get=lambda *a, **k: 1 / 0) == "off"
    monkeypatch.setattr("sgt.core.secrets.get_secret", lambda name, required=True: "https://probe.example/x")
    assert deadman.ping(get=lambda *a, **k: 1 / 0) == "failed"
    monkeypatch.setattr("sgt.core.secrets.get_secret", lambda *a, **k: 1 / 0)
    assert deadman.ping() == "off"


def test_the_control_job_calls_it_every_minute_after_everything_else():
    src = (Path(__file__).resolve().parents[2] / "scripts" / "sms_control.py").read_text()
    body = src.split("def cmd_poll")[1].split("\ndef ")[0]
    assert "deadman.ping(ok=not (chan_poll.active(now) and watchdog.silent(db, now)))" in body
    assert body.index("sms.close()") < body.index("deadman.ping(")
