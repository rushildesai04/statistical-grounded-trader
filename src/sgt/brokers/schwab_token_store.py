"""Writable home for Schwab's rotating refresh token on a headless host (red-team F-1, 2026-11-05).

On Fly there is no OS keychain and ``SGT_SECRET_*`` env is immutable, so a refresh token rotated by
``SchwabTokenManager._refresh`` could not be saved and the login would die within an hour (no exits possible).
When ``SGT_SECRET_STORE_DIR`` is set, the rotating values live in one JSON file there (dir 0700, file 0600,
atomic replace). The owner's weekly login on the Mac is pushed as env (``scripts/schwab_auth.py --push-fly``);
it is adopted only when its ``schwab_login_at`` is newer than the file's, so a fresh human login always wins and
a stale env value never overwrites a newer rotated token. All other secrets pass through ``get_secret``.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from sgt.core.secrets import MissingSecretError, get_secret

STORE_ENV = "SGT_SECRET_STORE_DIR"
FILE = "schwab_token.json"
ROTATING = ("schwab_refresh_token", "schwab_refresh_issued_at", "schwab_login_at")
MAX_BYTES = 16_384


def _ts(v: str | None) -> datetime | None:
    if not v:
        return None
    try:
        t = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


class FileTokenStore:
    def __init__(self, root: Path, base_get: Callable[..., "str | None"] = get_secret) -> None:
        self.root, self._base = Path(root), base_get
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)

    def get(self, name: str, *, required: bool = True) -> str | None:
        if name not in ROTATING:
            return self._base(name, required=required)
        value = self._current().get(name)
        if value is None and required:
            raise MissingSecretError(f"secret {name!r} not in the token store; run scripts/schwab_auth.py --push-fly")
        return value

    def set(self, name: str, value: str) -> None:
        if name not in ROTATING:
            raise ValueError(f"{name!r} is not a rotating Schwab secret")
        cur = self._current()
        cur[name] = value
        self._write(cur)

    def _read(self) -> dict[str, str]:
        p = self.root / FILE
        if not p.exists():
            return {}
        raw = p.read_bytes()
        if len(raw) > MAX_BYTES:
            raise ValueError("token store file too large")
        data = json.loads(raw)
        return {k: str(v) for k, v in data.items() if k in ROTATING and isinstance(v, str)} if isinstance(data, dict) \
            else {}

    def _current(self) -> dict[str, str]:
        file = self._read()
        env = {n: v for n in ROTATING if (v := self._base(n, required=False))}
        new, old = _ts(env.get("schwab_login_at")), _ts(file.get("schwab_login_at"))
        if env.get("schwab_refresh_token") and new is not None and (old is None or new > old):
            self._write(env)                       # a newer human login replaces whatever was rotated before
            return dict(env)
        return file

    def _write(self, data: dict[str, str]) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.root, prefix=".tok-")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.root / FILE)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def secret_io() -> tuple[Callable[..., "str | None"], Callable[[str, str], None]] | None:
    """(get, set) backed by the token store when ``SGT_SECRET_STORE_DIR`` is set (Fly); None = keychain defaults."""
    root = os.environ.get(STORE_ENV)
    if not root:
        return None
    store = FileTokenStore(Path(root))
    return store.get, store.set
