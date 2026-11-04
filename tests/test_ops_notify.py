"""ops_notify: plain-English failure texts, once per job per day, and the not-configured path (no network)."""
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops_notify  # noqa: E402

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 2, 20, 50, tzinfo=ET)
TRACEBACK = ("Traceback (most recent call last):\n  File \"/app/scripts/sms_control.py\", line 274\n"
             "    if cal.is_session(d) and now_et >= cal.bounds(d).close\n                ^^^^^^^^^^\n"
             "AttributeError: 'SessionBounds' object has no attribute 'close'\n!!! exit 1\n")


def test_message_is_plain_english_with_impact_and_no_code(tmp_path):
    # regression 2026-11-03: the owner received a raw Python traceback by SMS
    log = tmp_path / "run.log"
    log.write_text(TRACEBACK)
    msg = ops_notify.build_message("digest", log, NOW)
    assert msg.startswith("[SGT] Heads-up: the nightly digest did not finish (Mon 20:50 ET)")
    assert "internal error" in msg and "Trades and positions are unaffected" in msg
    for leak in ("Traceback", "AttributeError", "SessionBounds", "^^^", "exit 1", ".py", "line "):
        assert leak not in msg


def test_provider_outage_is_named_as_such(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("httpx.ReadTimeout: timed out\n!!! exit 1\n")
    assert "provider did not respond" in ops_notify.build_message("megacap", log, NOW)
    assert "a scheduled job" in ops_notify.build_message("unknown-job", tmp_path / "nope.log", NOW)


def test_one_text_per_job_per_day(tmp_path):
    assert ops_notify.first_today("digest", NOW, tmp_path)
    assert not ops_notify.first_today("digest", NOW, tmp_path)
    assert ops_notify.first_today("megacap", NOW, tmp_path)
    assert ops_notify.first_today("digest", datetime(2026, 11, 3, 20, 50, tzinfo=ET), tmp_path)
    assert ops_notify.first_today("../../etc", NOW, tmp_path) and not list(tmp_path.parent.glob("etc-*"))


def test_missing_twilio_config_exits_zero(monkeypatch, tmp_path):
    from sgt.core.secrets import MissingSecretError

    def boom():
        raise MissingSecretError("twilio_account_sid")
    monkeypatch.setattr(ops_notify, "TwilioSms", boom)
    monkeypatch.setattr(ops_notify, "ALERTS", tmp_path)
    monkeypatch.setattr(ops_notify, "first_today", lambda job: True)
    assert ops_notify.main(["digest"]) == 0
