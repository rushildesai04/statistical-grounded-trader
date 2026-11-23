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
    assert "deadman.ping(ok=not watchdog.silent(db, now))" in body       # ok = the listener's signs of life, any hour
    assert body.index("sms.close()") < body.index("deadman.ping(")


def test_a_call_that_hangs_is_cut_off_and_costs_the_control_job_a_bounded_time(monkeypatch):
    """An outage or a slow answer at the outside service (or a name lookup that never returns) must not hold the
    control job: the call runs on a thread that is abandoned after BOUND_S."""
    import threading
    import time

    monkeypatch.setattr("sgt.core.secrets.get_secret", lambda name, required=True: "https://probe.example/x")
    monkeypatch.setattr(deadman, "BOUND_S", 0.2)
    release = threading.Event()
    t0 = time.monotonic()
    assert deadman.ping(get=lambda url, timeout: release.wait(5)) == "failed"
    assert time.monotonic() - t0 < 1.0
    release.set()


def test_nothing_in_the_listener_or_the_books_ever_calls_the_outside_probe():
    """It cannot block a trading task because no trading task contains it: only the per-minute control job does, a
    separate process."""
    root = Path(__file__).resolve().parents[2]
    users = [p for p in [*root.glob("src/sgt/**/*.py"), *root.glob("scripts/*.py")]
             if "deadman" in p.read_text() and p.name not in ("deadman.py",)]
    assert [p.name for p in users] == ["sms_control.py"]
