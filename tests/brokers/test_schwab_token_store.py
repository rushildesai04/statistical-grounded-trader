from __future__ import annotations

import json
import stat

import pytest

from sgt.brokers import schwab_auth
from sgt.brokers.schwab_token_store import FILE, FileTokenStore, secret_io
from sgt.core.secrets import MissingSecretError


def env_get(values: dict):
    def get(name, *, required=True):
        v = values.get(name)
        if v is None and required:
            raise MissingSecretError(name)
        return v
    return get


def test_rotation_persists_privately_and_survives_a_new_process(tmp_path):
    env = {"schwab_app_key": "k", "schwab_refresh_token": "R0", "schwab_login_at": "2026-11-05T10:00:00+00:00"}
    s = FileTokenStore(tmp_path / "sec", env_get(env))
    assert s.get("schwab_refresh_token") == "R0"                      # adopted from the pushed login
    s.set("schwab_refresh_token", "R1")
    s.set("schwab_refresh_issued_at", "2026-11-05T10:30:00+00:00")
    again = FileTokenStore(tmp_path / "sec", env_get(env))            # another process / a restart
    assert again.get("schwab_refresh_token") == "R1" and again.get("schwab_app_key") == "k"
    p = tmp_path / "sec" / FILE
    assert stat.S_IMODE(p.stat().st_mode) == 0o600 and stat.S_IMODE((tmp_path / "sec").stat().st_mode) == 0o700
    assert json.loads(p.read_text())["schwab_login_at"] == "2026-11-05T10:00:00+00:00"
    assert not list((tmp_path / "sec").glob(".tok-*"))                # no temp files left behind


def test_only_a_newer_human_login_replaces_the_rotated_token(tmp_path):
    env = {"schwab_refresh_token": "OLD", "schwab_login_at": "2026-11-05T10:00:00+00:00"}
    s = FileTokenStore(tmp_path, env_get(env))
    s.get("schwab_refresh_token")
    s.set("schwab_refresh_token", "ROTATED")
    assert FileTokenStore(tmp_path, env_get(env)).get("schwab_refresh_token") == "ROTATED"   # stale env ignored
    env.update(schwab_refresh_token="WEEKLY", schwab_login_at="2026-11-12T10:00:00+00:00")
    assert FileTokenStore(tmp_path, env_get(env)).get("schwab_refresh_token") == "WEEKLY"


def test_missing_or_hostile_store_fails_closed(tmp_path):
    s = FileTokenStore(tmp_path, env_get({}))
    with pytest.raises(MissingSecretError):
        s.get("schwab_refresh_token")
    assert s.get("schwab_refresh_token", required=False) is None
    with pytest.raises(ValueError):
        s.set("schwab_app_secret", "x")                               # only rotating values are writable here
    (tmp_path / FILE).write_text("x" * 20_000)
    with pytest.raises(ValueError):
        s.get("schwab_refresh_token")


def test_token_manager_uses_the_store_when_configured(tmp_path, monkeypatch):
    monkeypatch.setenv("SGT_SECRET_STORE_DIR", str(tmp_path / "sec"))
    monkeypatch.setenv("SGT_SECRET_SCHWAB_APP_KEY", "key")
    monkeypatch.setenv("SGT_SECRET_SCHWAB_APP_SECRET", "secret")
    monkeypatch.setattr("keyring.get_password", lambda *a: None)
    get, put = secret_io()
    m = schwab_auth.SchwabTokenManager()
    assert m._get.__self__.__class__ is FileTokenStore and m._set.__self__.root == tmp_path / "sec"
    monkeypatch.delenv("SGT_SECRET_STORE_DIR")
    assert secret_io() is None
