"""Charles Schwab Trader API OAuth2 token manager.

Schwab individual Trader API uses an authorization-code OAuth2 flow with a
short-lived access token (~30 min) and a longer-lived refresh token (~7 days).
The refresh token is SINGLE-USE: every refresh returns a new refresh token and
invalidates the old one. We therefore persist the new refresh token to the OS
keychain BEFORE using the new access token, and we never blindly retry a refresh
(a timed-out refresh may have already spent the token).

Credentials are read only through ``sgt.core.secrets`` (OS keychain service ``sgt``
or ``SGT_SECRET_<NAME>`` env), never the repo:
- ``schwab_app_key``          developer app key (OAuth client_id)
- ``schwab_app_secret``       developer app secret (OAuth client_secret)
- ``schwab_refresh_token``    current refresh token (rotated on every refresh)
- ``schwab_refresh_issued_at`` ISO-8601 UTC timestamp the refresh token was issued

[VERIFY] against official Schwab developer docs before enabling a live account.
These facts are third-party-sourced and MUST be confirmed:
- Base host ``https://api.schwabapi.com``.
- Authorization endpoint ``GET /v1/oauth/authorize?client_id=..&redirect_uri=..``.
- Token endpoint ``POST /v1/oauth/token`` with HTTP Basic (app_key:app_secret),
  form body ``grant_type=authorization_code|refresh_token`` (+ ``code`` &
  ``redirect_uri`` for the code grant, ``refresh_token`` for the refresh grant).
- Token response fields ``access_token``, ``refresh_token``, ``expires_in``.
- Access-token lifetime ~1800 s; refresh-token lifetime ~7 days, single-use on
  refresh; expired refresh token requires full human re-authorization.
"""
from __future__ import annotations

import asyncio
import base64
import fcntl
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

from sgt.config import get_settings
from sgt.core.interfaces import BrokerAuthError
from sgt.core.secrets import get_secret, set_secret

log = logging.getLogger(__name__)

# [VERIFY] third-party facts — confirm against Schwab developer portal.
SCHWAB_BASE_URL = "https://api.schwabapi.com"
AUTHORIZE_PATH = "/v1/oauth/authorize"
TOKEN_PATH = "/v1/oauth/token"
ACCESS_TTL_S = 1800            # ~30 min; overridden by response ``expires_in``
REFRESH_TTL_DAYS = 7           # ~7 days, single-use on each refresh
_ACCESS_SKEW_S = 60            # refresh slightly early to avoid mid-call expiry
_LOCK_FILENAME = ".schwab_refresh.lock"  # cross-process advisory lock (under data_dir)

_RE_AUTH = (
    "re-authorization required: run `python scripts/schwab_auth.py` to obtain a new "
    "Schwab refresh token"
)

SecretGetter = Callable[..., "str | None"]
SecretSetter = Callable[[str, str], None]
Clock = Callable[[], datetime]


def basic_auth_header(app_key: str, app_secret: str) -> str:
    raw = f"{app_key}:{app_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SchwabTokenManager:
    """Caches the access token in memory; refreshes it with the keychain refresh token.

    All refresh outcomes fail closed to ``BrokerAuthError`` (``retryable=False``):
    a missing/expired/rejected refresh token, a malformed response, or a timed-out
    refresh (which may have spent the single-use token) all require human re-auth.

    Single-writer rotation: the single-use refresh token must never be spent by two
    concurrent refreshes. In-process, ``access_token`` serializes refreshes behind an
    ``asyncio.Lock`` (and re-checks the cache after acquiring it); use ``shared()`` so every
    adapter for one app credential reuses the SAME manager. Across processes, ``_refresh``
    holds an ``fcntl`` advisory lock on a file under ``settings.data_dir`` and re-reads the
    keychain refresh token after acquiring it, so a token another process rotated is used.
    """

    _registry: dict[str, "SchwabTokenManager"] = {}
    _registry_lock = threading.Lock()

    @classmethod
    def shared(
        cls,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
        timeout: float | None = None,
        secret_get: SecretGetter = get_secret,
        secret_set: SecretSetter = set_secret,
    ) -> "SchwabTokenManager":
        """One manager per app key in this process (single-writer for token rotation)."""
        app_key = secret_get("schwab_app_key")
        if not app_key:
            raise BrokerAuthError(f"no Schwab app key stored; {_RE_AUTH}")
        with cls._registry_lock:
            mgr = cls._registry.get(app_key)
            if mgr is None:
                mgr = cls(client=client, clock=clock, timeout=timeout,
                          secret_get=secret_get, secret_set=secret_set)
                cls._registry[app_key] = mgr
            return mgr

    @classmethod
    def _reset_registry_for_tests(cls) -> None:
        with cls._registry_lock:
            cls._registry.clear()

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        clock: Clock | None = None,
        timeout: float | None = None,
        secret_get: SecretGetter = get_secret,
        secret_set: SecretSetter = set_secret,
    ) -> None:
        self._get = secret_get
        self._set = secret_set
        self._app_key = self._get("schwab_app_key")
        self._app_secret = self._get("schwab_app_secret")
        assert self._app_key is not None and self._app_secret is not None
        self._auth_header = basic_auth_header(self._app_key, self._app_secret)
        self._timeout = timeout if timeout is not None else get_settings().http_timeout_s
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._owns_client = client is None
        self._client = client
        self._access_token: str | None = None
        self._access_expires_at: datetime | None = None
        self._refresh_lock = asyncio.Lock()
        self._lock_fh = None

    def _now(self) -> datetime:
        return self._clock()

    def _is_fresh(self) -> bool:
        return (
            self._access_token is not None
            and self._access_expires_at is not None
            and self._now() < self._access_expires_at - timedelta(seconds=_ACCESS_SKEW_S)
        )

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=SCHWAB_BASE_URL, timeout=httpx.Timeout(self._timeout)
            )
        return self._client

    async def access_token(self, *, force: bool = False) -> str:
        """Return a valid access token, refreshing via the refresh token if needed.

        Refreshes are serialized behind ``_refresh_lock`` so concurrent callers in this
        process issue AT MOST ONE token POST: the first refreshes, the rest observe the
        freshly cached token after acquiring the lock and return it without a second POST.
        """
        before = self._access_token
        if not force and self._is_fresh():
            return before  # type: ignore[return-value]
        async with self._refresh_lock:
            # Another coroutine may have refreshed while we waited for the lock.
            if self._access_token is not None and self._access_token is not before and self._is_fresh():
                return self._access_token
            if not force and self._is_fresh():
                return self._access_token
            await self._refresh()
        assert self._access_token is not None
        return self._access_token

    async def _refresh(self) -> None:
        # Cross-process advisory lock: serialize single-use refresh-token rotation so two
        # processes never spend the same token simultaneously.
        await asyncio.to_thread(self._acquire_file_lock)
        try:
            # Re-read under the lock: another process may have rotated the token already.
            refresh_token = self._get("schwab_refresh_token", required=False)
            if not refresh_token:
                raise BrokerAuthError(f"no Schwab refresh token stored; {_RE_AUTH}")
            expires_at = self.refresh_expires_at()
            if expires_at is not None and self._now() >= expires_at:
                raise BrokerAuthError(f"Schwab refresh token expired; {_RE_AUTH}")

            data = {"grant_type": "refresh_token", "refresh_token": refresh_token}
            headers = {
                "Authorization": self._auth_header,
                "Content-Type": "application/x-www-form-urlencoded",
            }
            try:
                resp = await self._http().post(TOKEN_PATH, data=data, headers=headers)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                # A timed-out/failed refresh may have already consumed the single-use
                # token. Never retry blindly — fail closed and require re-auth.
                log.warning("schwab token refresh transport error: %s", type(exc).__name__)
                raise BrokerAuthError(
                    f"Schwab token refresh failed ({type(exc).__name__}); the single-use "
                    f"refresh token may be spent; {_RE_AUTH}"
                ) from exc
            if resp.status_code != 200:
                raise BrokerAuthError(
                    f"Schwab token refresh rejected (HTTP {resp.status_code}); {_RE_AUTH}"
                )
            body = resp.json()
            new_refresh = body.get("refresh_token")
            access = body.get("access_token")
            if not access or not new_refresh:
                raise BrokerAuthError(f"Schwab token response missing tokens; {_RE_AUTH}")
            try:
                expires_in = int(body.get("expires_in", ACCESS_TTL_S))
            except (TypeError, ValueError):
                expires_in = ACCESS_TTL_S
            issued = self._now()
            # Persist the rotated single-use refresh token BEFORE using the new access
            # token, so a crash after this point cannot strand us with a spent token.
            self._set("schwab_refresh_token", new_refresh)
            self._set("schwab_refresh_issued_at", issued.isoformat())
            self._access_token = access
            self._access_expires_at = issued + timedelta(seconds=expires_in)
        finally:
            await asyncio.to_thread(self._release_file_lock)

    def _acquire_file_lock(self) -> None:
        data_dir = get_settings().data_dir
        os.makedirs(data_dir, exist_ok=True)
        self._lock_fh = open(os.path.join(data_dir, _LOCK_FILENAME), "w")
        fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_EX)

    def _release_file_lock(self) -> None:
        if self._lock_fh is not None:
            try:
                fcntl.flock(self._lock_fh.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_fh.close()
                self._lock_fh = None

    def refresh_expires_at(self) -> datetime | None:
        """When the stored refresh token lapses (issued_at + ~7 days), or None."""
        issued_raw = self._get("schwab_refresh_issued_at", required=False)
        if not issued_raw:
            return None
        try:
            issued = _parse_iso(issued_raw)
        except ValueError:
            return None
        return issued + timedelta(days=REFRESH_TTL_DAYS)

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None
