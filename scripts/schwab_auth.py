"""Interactive Schwab OAuth2 helper: obtain / rotate the refresh token.

Schwab's refresh token lapses after ~7 days, so this is both the one-time setup
step and a weekly re-authorization chore. It prints the authorize URL, waits for
the operator to log in and paste the full redirected URL, extracts the
authorization ``code``, exchanges it for tokens, and stores the refresh token and
its issue time in the OS keychain. Access tokens are never persisted (they live in
memory inside ``SchwabTokenManager``). Secrets are never printed.

Prerequisites (in the keychain, service ``sgt``, or ``SGT_SECRET_*`` env):
    schwab_app_key      developer app key
    schwab_app_secret   developer app secret

Usage:
    python scripts/schwab_auth.py
    python scripts/schwab_auth.py --redirect-uri https://127.0.0.1

The redirect URI MUST exactly match one registered on the Schwab developer app.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from sgt.brokers.schwab_auth import (
    AUTHORIZE_PATH,
    REFRESH_TTL_DAYS,
    SCHWAB_BASE_URL,
    TOKEN_PATH,
    basic_auth_header,
)
from sgt.config import get_settings
from sgt.core.secrets import get_secret, set_secret

_DEFAULT_REDIRECT = "https://127.0.0.1"


def _extract_code(pasted: str) -> str:
    """Pull the ``code`` query param out of the pasted redirect URL."""
    parsed = urlparse(pasted.strip())
    qs = parse_qs(parsed.query)
    code = qs.get("code", [None])[0]
    if not code:
        raise SystemExit("could not find a 'code' parameter in the pasted URL")
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Schwab OAuth2 re-authorization helper")
    parser.add_argument("--redirect-uri", default=_DEFAULT_REDIRECT,
                        help="redirect URI registered on the Schwab app "
                             f"(default {_DEFAULT_REDIRECT})")
    args = parser.parse_args(argv)

    app_key = get_secret("schwab_app_key")
    app_secret = get_secret("schwab_app_secret")
    assert app_key is not None and app_secret is not None

    authorize_url = (
        f"{SCHWAB_BASE_URL}{AUTHORIZE_PATH}?"
        + urlencode({"client_id": app_key, "redirect_uri": args.redirect_uri})
    )
    print("1. Open this URL in a browser and log in to Schwab:\n")
    print(f"   {authorize_url}\n")
    print("2. After approving, the browser is redirected to your redirect URI.")
    print("   Copy the FULL redirected URL from the address bar and paste it below.\n")
    pasted = input("Redirected URL: ").strip()
    code = _extract_code(pasted)

    timeout = get_settings().http_timeout_s
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": args.redirect_uri,
    }
    headers = {
        "Authorization": basic_auth_header(app_key, app_secret),
        "Content-Type": "application/x-www-form-urlencoded",
    }
    with httpx.Client(base_url=SCHWAB_BASE_URL, timeout=timeout) as client:
        resp = client.post(TOKEN_PATH, data=data, headers=headers)
    if resp.status_code != 200:
        raise SystemExit(f"token exchange failed: HTTP {resp.status_code}")
    body = resp.json()
    refresh_token = body.get("refresh_token")
    if not refresh_token or not body.get("access_token"):
        raise SystemExit("token response did not include the expected tokens")

    issued = datetime.now(timezone.utc)
    set_secret("schwab_refresh_token", refresh_token)
    set_secret("schwab_refresh_issued_at", issued.isoformat())

    print("\nStored a new Schwab refresh token in the keychain (not printed).")
    print(f"Re-authorization is required again within ~{REFRESH_TTL_DAYS} days "
          f"(by {(issued).date().isoformat()} + {REFRESH_TTL_DAYS}d).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
