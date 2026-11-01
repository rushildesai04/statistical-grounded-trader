"""ops_notify: message building and the not-configured path (no network)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ops_notify  # noqa: E402


def test_message_includes_log_tail(tmp_path):
    log = tmp_path / "run.log"
    log.write_text("a\nb\n\nTraceback x\nLiveError: boom\n!!! exit 1\n")
    msg = ops_notify.build_message("megacap job failed (exit 1)", log)
    assert msg.splitlines() == ["[sgt-runner] megacap job failed (exit 1)", "Traceback x", "LiveError: boom", "!!! exit 1"]


def test_missing_log_is_title_only(tmp_path):
    assert ops_notify.build_message("x", tmp_path / "nope.log") == "[sgt-runner] x"


def test_not_configured_exits_zero_and_says_so(monkeypatch, capsys):
    from sgt.core.secrets import MissingSecretError

    def boom(*a, **k):
        raise MissingSecretError("twilio_account_sid")
    monkeypatch.setattr(ops_notify, "TwilioSms", boom)
    assert ops_notify.main(["job failed"]) == 0
    assert "Twilio not configured" in capsys.readouterr().err
