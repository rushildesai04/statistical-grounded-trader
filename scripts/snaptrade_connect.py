"""SnapTrade operator helper: register the user, open the connection portal, list links.

SnapTrade brokers one signed API across many brokerages. This script performs the one-time
(and occasionally repeated) operator chores:

1. Registers the SnapTrade user if no ``snaptrade_user_secret`` is stored yet, saving the
   returned userSecret in the OS keychain (it is returned exactly once by SnapTrade).
2. Prints the connection-portal URL. Open it in a browser and link each brokerage
   (Robinhood / Schwab / Webull / E*TRADE / tastytrade / Public / moomoo / ...).
3. Lists every linked account with its SnapTrade id and connection type (READ / TRADE) so
   you know which accounts ``SnapTradeBroker(trade_enabled=True)`` may trade and which must
   stay read-only (feeding reconciliation + AlertOnlyBroker for manual execution).

Secrets are NEVER printed. The connection-portal URL is a short-lived redirect that itself
contains no partner secret.

Prerequisites (keychain service ``sgt`` or ``SGT_SECRET_*`` env):
    snaptrade_client_id      partner clientId
    snaptrade_consumer_key   partner consumerKey
    snaptrade_user_id        your chosen stable, unique user id (not an email)

Usage:
    python scripts/snaptrade_connect.py
    python scripts/snaptrade_connect.py --no-portal   # skip portal, just list accounts
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sgt.brokers.snaptrade_client import SnapTradeClient
from sgt.config import get_settings
from sgt.core.secrets import get_secret, set_secret


async def _run(show_portal: bool) -> int:
    client_id = get_secret("snaptrade_client_id")
    consumer_key = get_secret("snaptrade_consumer_key")
    user_id = get_secret("snaptrade_user_id")
    assert client_id is not None and consumer_key is not None and user_id is not None
    user_secret = get_secret("snaptrade_user_secret", required=False) or ""

    client = SnapTradeClient(
        client_id=client_id,
        consumer_key=consumer_key,
        user_id=user_id,
        user_secret=user_secret,
        timeout_s=get_settings().http_timeout_s,
    )
    try:
        if not user_secret:
            print("No snaptrade_user_secret found; registering the SnapTrade user ...")
            resp = await client.register_user()
            user_secret = str(resp.get("userSecret", ""))
            if not user_secret:
                raise SystemExit("registration did not return a userSecret")
            set_secret("snaptrade_user_secret", user_secret)
            print("Stored a new SnapTrade userSecret in the keychain (not printed).")
            client = SnapTradeClient(
                client_id=client_id, consumer_key=consumer_key, user_id=user_id,
                user_secret=user_secret, timeout_s=get_settings().http_timeout_s)

        if show_portal:
            portal = await client.login_portal_url()
            url = str(portal.get("redirectURI", "")) if isinstance(portal, dict) else ""
            if not url:
                raise SystemExit("login did not return a redirectURI")
            print("\nOpen this connection-portal URL and link your brokerage(s):\n")
            print(f"   {url}\n")
            input("Press Enter once you have finished linking in the browser ...")

        await _list_accounts(client)
    finally:
        await client.close()
    return 0


async def _list_accounts(client: SnapTradeClient) -> None:
    auths = {str(a.get("id")): a for a in await client.list_authorizations()}
    accounts = await client.list_accounts()
    if not accounts:
        print("\nNo linked accounts yet. Re-run after linking a brokerage in the portal.")
        return
    print(f"\nLinked SnapTrade accounts ({len(accounts)}):\n")
    print(f"  {'TYPE':<6} {'SNAPTRADE_ACCOUNT_ID':<40} {'INSTITUTION':<22} ACCOUNT#")
    for acct in accounts:
        auth = auths.get(str(acct.get("brokerage_authorization")), {})
        conn_type = str(auth.get("type", "")).lower()
        label = "TRADE" if conn_type == "trade" else "READ"
        if auth.get("disabled"):
            label += "*"  # disabled connection: data is stale, re-link needed
        print(f"  {label:<6} {str(acct.get('id','')):<40} "
              f"{str(acct.get('institution_name','')):<22} {acct.get('number','')}")
    print("\n  TRADE -> SnapTradeBroker(trade_enabled=True); READ -> ReadOnlyPositionSource "
          "+ AlertOnlyBroker. '*' = disabled connection (re-link in the portal).")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SnapTrade connect / list helper")
    parser.add_argument("--no-portal", action="store_true",
                        help="skip the connection portal step and only list linked accounts")
    args = parser.parse_args(argv)
    return asyncio.run(_run(show_portal=not args.no_portal))


if __name__ == "__main__":
    sys.exit(main())
