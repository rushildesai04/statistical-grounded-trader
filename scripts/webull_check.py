"""Webull connection check through the official OpenAPI. Places nothing in a real account.

    python scripts/webull_check.py --sandbox            # the simulated environment (keys: webull_sandbox_app_key / _secret)
    python scripts/webull_check.py --sandbox --preview  # also asks Webull to PREVIEW a 1-share limit order (no order is placed)
    python scripts/webull_check.py --sandbox --paper-order   # places and cancels ONE simulated order in the sandbox
    python scripts/webull_check.py                      # the real account, READ-ONLY (keys: webull_app_key / _secret,
                                                        # webull_access_token); orders are switched off in this script

Keys come from the keychain / ``SGT_SECRET_*`` only. Account ids, keys and tokens are never printed. What IS printed
is the SHAPE of each answer (field names and value types): the response fields are not in the public docs, and the
adapter is built from what the sandbox really returns.
"""
from __future__ import annotations

import argparse
import asyncio
import uuid

from sgt.brokers.webull_client import WebullClient, stock_order
from sgt.core.interfaces import BrokerError
from sgt.core.secrets import get_secret

HIDE = ("account", "user", "token", "key", "secret", "number", "id")


def shape(x, depth: int = 0):
    """Field names and value types of an answer; values are shown only for short enum-like strings in fields that
    cannot identify the account. Lists show their first item's shape."""
    if isinstance(x, dict):
        return {str(k)[:40]: ("<hidden>" if any(w in str(k).lower() for w in HIDE) and not isinstance(v, (dict, list)) else
                              shape(v, depth + 1)) for k, v in list(x.items())[:60]} if depth < 5 else "{...}"
    if isinstance(x, list):
        return [shape(x[0], depth + 1), f"... {len(x)} items"] if x else []
    if isinstance(x, str):
        return x if len(x) <= 24 and x.replace("_", "").isalpha() and x.isupper() else f"<str {len(x)}>"
    return type(x).__name__


def first_account(accounts) -> str | None:
    rows = accounts if isinstance(accounts, list) else (accounts.get("data") or accounts.get("accounts") or []) if isinstance(accounts, dict) else []
    for a in rows:
        if isinstance(a, dict) and isinstance(a.get("account_id"), str):
            return a["account_id"]
    return None


async def check(sandbox: bool, preview: bool, paper_order: bool, symbol: str, price: str) -> int:
    pre = "webull_sandbox" if sandbox else "webull"
    c = WebullClient(app_key=get_secret(f"{pre}_app_key"), app_secret=get_secret(f"{pre}_app_secret"), sandbox=sandbox,
                     orders="sandbox" if (sandbox and paper_order) else "none",
                     access_token=None if sandbox else get_secret("webull_access_token", required=False))
    try:
        accounts = await c.accounts()
        print(f"Webull {'SANDBOX (simulated)' if sandbox else 'REAL account, read-only'}: connected")
        print("  accounts:", shape(accounts))
        acct = first_account(accounts)
        if acct is None:
            print("  no account id found in the answer: stop here and read the shape above")
            return 1
        print("  balance:", shape(await c.balance(acct)))
        print("  positions:", shape(await c.positions(acct)))
        print("  open orders:", shape(await c.open_orders(acct)))
        try:
            print(f"  snapshot {symbol}:", shape(await c.snapshot([symbol])))
        except BrokerError as e:                   # market data needs its own subscription: said, not fatal
            print(f"  snapshot not available: {e}")
        if preview or paper_order:
            cid = uuid.uuid4().hex
            order = stock_order(client_order_id=cid, symbol=symbol, side="BUY", quantity="1", order_type="LIMIT", limit_price=price)
            print("  preview (nothing placed):", shape(await c.preview(acct, order)))
            if paper_order:                        # sandbox only: the client refuses this on the production host
                print("  SIMULATED order placed:", shape(await c.place(acct, order)))
                print("  order detail:", shape(await c.order(acct, cid)))
                print("  cancel:", shape(await c.cancel(acct, cid)))
    finally:
        await c.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sandbox", action="store_true", help="the simulated environment")
    ap.add_argument("--preview", action="store_true", help="ask for an order preview (never places an order)")
    ap.add_argument("--paper-order", action="store_true", help="sandbox only: place and cancel one simulated order")
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--price", default="1.00", help="limit price of the test order (far below the market: it rests, then is cancelled)")
    a = ap.parse_args(argv)
    if a.paper_order and not a.sandbox:
        print("--paper-order is for the sandbox only; this script never places an order in a real account")
        return 2
    try:
        return asyncio.run(check(a.sandbox, a.preview, a.paper_order, a.symbol, a.price))
    except BrokerError as e:
        print(f"Webull check failed: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
