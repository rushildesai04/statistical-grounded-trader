"""Phase 1 of the Schwab live pilot (docs/research/live_pilot_schwab_2026-11-05.md): READ-ONLY connection check.

Places no orders. Resolves the account by its last 4 digits, reads balances and positions, and reports what the
pilot needs to know (cash, buying power, margin vs cash account hint, number of positions). Account numbers and
hashes are never printed.

    python scripts/schwab_check.py --last4 1234
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from sgt.core.interfaces import BrokerError
from sgt.brokers.schwab import SchwabBroker


async def check(last4: str) -> int:
    b = SchwabBroker("schwab_live", last4)
    try:
        await b.connect()
        st = await b.account_state()
        h = await b.health()
        flags = await b.account_flags()
        signed = await b.signed_positions()
    finally:
        await b.close()
    print("Schwab read-only check: connected, account resolved (number not shown)")
    print(f"  cash ${st.cash:,.2f} | settled ${st.settled_cash:,.2f} | buying power ${st.buying_power:,.2f}")
    print(f"  positions held: {len(st.positions)} (the pilot will never sell these)")
    print(f"  margin hint: {'buying power above cash, likely a MARGIN account (shorts possible)' if st.buying_power > st.cash + 1 else 'buying power equals cash, likely a CASH account (no shorts)'}")
    print(f"  adapter health: {h}")
    # 2026-11-05 F-2 [VERIFY]: these must all be present (not None) or the pilot's PDT guard blocks real fades.
    print("  day-trade fields: " + ", ".join(f"{k}={'MISSING' if v is None else v}" for k, v in flags.items()
                                             if k != "equity") + f", equity {'MISSING' if flags['equity'] is None else 'present'}")
    shorts = sum(1 for q in signed.values() if q < 0)
    print(f"  signed positions read: {len(signed)} symbols, {shorts} short")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--last4", required=True, help="last 4 digits of the Schwab account to use")
    a = ap.parse_args()
    if not (len(a.last4) == 4 and a.last4.isdigit()):
        print("--last4 must be 4 digits", file=sys.stderr)
        return 2
    try:
        return asyncio.run(check(a.last4))
    except BrokerError as exc:
        print(f"Schwab check failed: {type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
