"""Secret retrieval: OS keychain first, environment second, never the repo.

Broker keys, OAuth tokens and the control-plane token are read through
``get_secret``. Lookup order:
1. OS keychain via ``keyring`` (service ``"sgt"``, username = secret name)
2. Environment variable ``SGT_SECRET_<NAME>`` (for CI / containers)

A missing required secret raises ``MissingSecretError`` — callers fail closed.
Secret values are never logged; ``redact`` is provided for error messages.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

KEYCHAIN_SERVICE = "sgt"


class MissingSecretError(RuntimeError):
    pass


def _env_name(name: str) -> str:
    return "SGT_SECRET_" + name.upper().replace("-", "_").replace(".", "_")


def get_secret(name: str, *, required: bool = True) -> str | None:
    value: str | None = None
    try:
        import keyring  # optional dependency; absence is not an error
        from keyring.errors import KeyringError

        try:
            value = keyring.get_password(KEYCHAIN_SERVICE, name)
        except KeyringError as exc:  # backend unavailable (headless/CI)
            log.debug("keychain lookup for %s unavailable: %s", name, type(exc).__name__)
    except ImportError:
        pass
    if not value:
        value = os.environ.get(_env_name(name)) or None
    if value is None and required:
        raise MissingSecretError(
            f"secret {name!r} not found in keychain service {KEYCHAIN_SERVICE!r} or env {_env_name(name)}"
        )
    return value


def set_secret(name: str, value: str) -> None:
    """Store a secret in the OS keychain (used by OAuth token refresh flows)."""
    import keyring

    keyring.set_password(KEYCHAIN_SERVICE, name, value)


def redact(value: str | None) -> str:
    if not value:
        return "<empty>"
    return value[:2] + "…" + f"<{len(value)} chars>"
