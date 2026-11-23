"""Session-hours watchdog: a silent listener is texted from a separate process (operational risk review, 2026-11-23)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sgt.ops import watchdog as wd
from sgt.ops.store import OpsStore, iso

ET = ZoneInfo("America/New_York")
NOON = datetime(2026, 11, 23, 12, 0, tzinfo=ET).astimezone(timezone.utc)      # a session, alert hours


def beat(db, at):
    db.set_flag("fade_heartbeat", iso(at))


def test_a_listener_that_runs_is_never_texted_about(tmp_path):
    db, sent = OpsStore(tmp_path / "ops.db"), []
    assert wd.check(db, sent.append, NOON) is None           # never ran: the pre-market check's business
    beat(db, NOON - timedelta(seconds=15))
    assert wd.check(db, sent.append, NOON) is None
    beat(db, NOON - wd.STALE)
    assert wd.check(db, sent.append, NOON) is None and sent == []


def test_a_silent_listener_in_alert_hours_is_texted_once_then_every_half_hour_with_what_is_open(tmp_path):
    db, sent = OpsStore(tmp_path / "ops.db"), []
    beat(db, NOON - timedelta(minutes=4))
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, created_at) "
                  "VALUES ('AAA','2026-11-23','new_high','x','-1',1,'open','x')")
    text = wd.check(db, sent.append, NOON)
    assert sent == [text] and "has not run for 4 min (last pass 11:56 ET)" in text
    assert "Open on paper: 1 short, 0 long. Their stops are NOT being checked." in text
    for m in (1, 15, 29):                                    # still down: not again inside the half hour
        assert wd.check(db, sent.append, NOON + timedelta(minutes=m)) is None
    again = wd.check(db, sent.append, NOON + timedelta(minutes=31))
    assert len(sent) == 2 and "has not run for 35 min" in again
    beat(db, NOON + timedelta(minutes=40))                   # back: silent again
    assert wd.check(db, sent.append, NOON + timedelta(minutes=40, seconds=30)) is None and len(sent) == 2


def test_a_warning_that_could_not_be_sent_is_tried_again_the_next_minute(tmp_path):
    db, sent, fail = OpsStore(tmp_path / "ops.db"), [], [True]
    beat(db, NOON - timedelta(minutes=5))

    def send(text):
        if fail[0]:
            raise OSError("the text service is down")
        sent.append(text)

    assert wd.check(db, send, NOON) is None and db.flag(wd.FLAG) is None      # failed: not marked as told
    fail[0] = False
    assert "has not run for 6 min" in wd.check(db, send, NOON + timedelta(minutes=1)) and len(sent) == 1


def test_outside_alert_hours_a_stale_heartbeat_is_not_a_text(tmp_path):
    db, sent = OpsStore(tmp_path / "ops.db"), []
    night = datetime(2026, 11, 23, 23, 30, tzinfo=ET).astimezone(timezone.utc)   # a deploy at night
    beat(db, night - timedelta(minutes=10))
    assert wd.check(db, sent.append, night) is None
    sunday = datetime(2026, 11, 22, 12, 0, tzinfo=ET).astimezone(timezone.utc)
    beat(db, sunday - timedelta(hours=3))
    assert wd.check(db, sent.append, sunday) is None and sent == []


def test_the_watchdog_is_capped_and_never_raises_into_the_control_job(tmp_path, monkeypatch):
    db, sent = OpsStore(tmp_path / "ops.db"), []
    beat(db, NOON - timedelta(hours=1))
    for k in range(wd.CAP + 3):                              # stuck all day: the cap holds
        wd.check(db, sent.append, NOON + k * (wd.AGAIN + timedelta(minutes=1)))
    assert sum("has not run" in t for t in sent) == wd.CAP
    monkeypatch.setattr(db, "flag", lambda *a, **k: 1 / 0)
    assert wd.check(db, sent.append, NOON) is None           # a broken database: logged, the job goes on


def test_the_control_job_runs_the_watchdog_every_minute():
    from pathlib import Path

    src = (Path(__file__).resolve().parents[2] / "scripts" / "sms_control.py").read_text()
    assert "watchdog.check(db, sms.send, now)" in src.split("def cmd_poll")[1][:900]


# ---- what "silent" means: three signs of life, none of them an alert arriving (2026-11-23) -------------------------

def _alive(db, folder, at, short=True, long=True, alerts=True):
    import os

    from sgt.ops import own_thread

    if short:
        beat(db, at)
    for name, on in (("long book", long), ("alerts", alerts)):
        own_thread.beat(folder, name)
        t = at.timestamp() if on else at.timestamp() - 900
        os.utime(folder / f"beat.{name.replace(' ', '_')}", (t, t))


def test_a_quiet_channel_is_not_a_silent_listener(tmp_path):
    """No alert all morning and nothing open: the listener still shows all three signs of life, so no text."""
    db, sent = OpsStore(tmp_path / "ops.db"), []
    _alive(db, tmp_path, NOON - timedelta(seconds=10))
    assert wd.silent(db, NOON) == {} and wd.check(db, sent.append, NOON) is None and sent == []
    with db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM fade_trades").fetchone()[0] == 0     # not one alert, and still healthy


def test_a_dead_alert_intake_is_reported_while_the_stops_still_run_and_the_text_says_which(tmp_path):
    db, sent = OpsStore(tmp_path / "ops.db"), []
    _alive(db, tmp_path, NOON - timedelta(seconds=10), alerts=False)                # Telegram connection gone, books fine
    assert list(wd.silent(db, NOON)) == ["alerts"]
    text = wd.check(db, sent.append, NOON)
    assert "the alert channel has not been read for 15 min: new alerts are NOT reaching the books" in text
    assert "The short book's stop checks are running." in text and "has not run for" not in text
    db2, sent2 = OpsStore(tmp_path / "b" / "ops.db"), []
    _alive(db2, tmp_path / "b", NOON - timedelta(seconds=10), long=False)           # the long book's thread is stuck
    with db2.tx() as c:
        c.execute("INSERT INTO long_trades(fade_id, symbol, session, status, created_at) VALUES (1,'AAA','2026-11-23','open','x')")
    assert "the long book has not checked its positions for 15 min (1 open on paper)" in wd.check(db2, sent2.append, NOON)


def test_with_the_fast_channel_check_switched_off_five_minutes_between_reads_is_normal(tmp_path):
    import os

    from sgt.fast import chan_poll
    from sgt.ops import own_thread

    db = OpsStore(tmp_path / "ops.db")
    db.set_flag(chan_poll.FLAG, "0")
    _alive(db, tmp_path, NOON - timedelta(seconds=10))
    t = NOON.timestamp() - 600                               # read 10 minutes ago: inside 3 passes of 5 minutes
    os.utime(tmp_path / "beat.alerts", (t, t))
    assert wd.silent(db, NOON) == {}
    t = NOON.timestamp() - 1000
    os.utime(tmp_path / "beat.alerts", (t, t))
    assert list(wd.silent(db, NOON)) == ["alerts"]
    assert own_thread.beat_age(tmp_path, "never beat", NOON.timestamp()) is None    # unknown is not silent


def test_a_process_that_hangs_is_noticed_from_outside_it(tmp_path, monkeypatch):
    """A real listener stand-in: a separate process that beats ten times a second, then is frozen with SIGSTOP (it
    is alive, it holds its files, it does nothing: a hang). The watchdog runs HERE, in another process, and reads
    only what the frozen one left on disk."""
    import os
    import signal
    import subprocess
    import sys
    import time
    from pathlib import Path

    import sgt

    db, sent = OpsStore(tmp_path / "ops.db"), []
    code = ("import sys, time\nfrom pathlib import Path\nfrom sgt.ops.store import OpsStore, iso, utcnow\n"
            "from sgt.ops import own_thread\nd = Path(sys.argv[1])\ndb = OpsStore(d / 'ops.db')\n"
            "while True:\n    db.set_flag('fade_heartbeat', iso(utcnow()))\n"
            "    own_thread.beat(d, 'long book'); own_thread.beat(d, 'alerts')\n    time.sleep(0.1)\n")
    env = {**os.environ, "PYTHONPATH": str(Path(sgt.__file__).resolve().parents[1])}
    child = subprocess.Popen([sys.executable, "-W", "ignore", "-c", code, str(tmp_path)], env=env)
    try:
        now = lambda: datetime.now(timezone.utc)  # noqa: E731
        end = time.time() + 20
        while db.flag("fade_heartbeat") is None or not (tmp_path / "beat.alerts").exists():
            assert time.time() < end and child.poll() is None, "the stand-in never started"
            time.sleep(0.05)
        monkeypatch.setattr(wd, "STALE", timedelta(seconds=1.0))
        monkeypatch.setattr("sgt.fast.chan_poll.interval", lambda v: 0.2)   # so the alerts limit is STALE too
        time.sleep(0.3)
        assert wd.check(db, sent.append, now(), in_hours=lambda t: True) is None   # running: nothing to say
        os.kill(child.pid, signal.SIGSTOP)                   # hung
        time.sleep(1.6)
        assert child.poll() is None                          # still a live process
        text = wd.check(db, sent.append, now(), in_hours=lambda t: True)
        assert sorted(wd.silent(db, now())) == ["alerts", "long book", "short book"]          # all three signs gone
        assert text and "The listener has not run for 0 min" in text and "Alerts are not being read." in text
        os.kill(child.pid, signal.SIGCONT)                   # it comes back: quiet again
        time.sleep(0.6)
        assert wd.silent(db, now()) == {}
    finally:
        child.kill()
        child.wait()
