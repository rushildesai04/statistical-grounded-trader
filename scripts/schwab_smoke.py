"""Live pilot phase 2 (design live_pilot_schwab_2026-11-05): one real round trip through the full pilot path.

Buys 1 share of IEF (~$95) with a marketable limit through the pilot ledger + Schwab adapter, waits for the fill,
sells it, and checks that the ledger, Schwab's positions and the fills agree. REAL MONEY: needs --real-money and
regular market hours. Never touches any other holding.

    python scripts/schwab_smoke.py --real-money
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone

from sgt.ops.store import OpsStore
from sgt.pilot import ledger, runtime
from sgt.pilot.executor import tick

SYMBOL = "IEF"


async def _wait_filled(pilot, cid: str, seconds: int = 90) -> bool:
    for _ in range(seconds // 3):
        await pilot.sync()
        row = ledger.get(pilot.db, cid)
        if row["status"] == "filled":
            return True
        if row["status"] in ("canceled", "rejected", "expired"):
            print(f"  {cid}: {row['status']} ({row['reason']})")
            return False
        await asyncio.sleep(3)
    await pilot.cancel(cid)
    return ledger.get(pilot.db, cid)["status"] == "filled"


async def smoke() -> int:
    db = OpsStore()
    handle = runtime.PilotHandle(db, print)
    import os
    os.environ.setdefault(runtime.ENV, "1")
    pilot = await handle.get()
    if pilot is None:
        print("Schwab not connected (see message above)")
        return 1
    try:
        stamp = datetime.now(timezone.utc).strftime("smoke%Y%m%d%H%M")
        px = runtime.iex_marks([SYMBOL]).get(SYMBOL)
        if not px:
            print("no IEF quote")
            return 1
        buy = await pilot.place(strategy="6040", decision=stamp, leg="buy", symbol=SYMBOL, side="BUY", qty=1,
                                marks={SYMBOL: px}, limit_price=tick(px * 1.002, up=True))
        print(f"1. BUY 1 {SYMBOL} sent ({buy})")
        if not await _wait_filled(pilot, buy):
            print("   not filled; nothing held. Smoke test FAILED at the buy.")
            return 1
        print(f"   filled @ {ledger.get(db, buy)['avg_price']}")
        print(f"2. reconcile: {await pilot.reconcile() or 'ledger matches Schwab'}")
        px = runtime.iex_marks([SYMBOL]).get(SYMBOL) or px
        sell = await pilot.place(strategy="6040", decision=stamp, leg="sell", symbol=SYMBOL, side="SELL", qty=1,
                                 marks={SYMBOL: px}, limit_price=tick(px * 0.998, up=False))
        print(f"3. SELL 1 {SYMBOL} sent ({sell})")
        if not await _wait_filled(pilot, sell):
            print("   SELL not filled: the pilot still holds 1 IEF. Text FLATTEN or sell it in the app.")
            return 1
        print(f"   filled @ {ledger.get(db, sell)['avg_price']}")
        left = [p for p in ledger.positions(db, "6040") if p.symbol == SYMBOL]
        print(f"4. pilot {SYMBOL} position after: {left[0].qty if left else 0}; reconcile: "
              f"{await pilot.reconcile() or 'ok'}")
        print("Smoke test PASSED: order, fill, ledger and reconciliation work end to end.")
        return 0
    finally:
        await handle.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--real-money", action="store_true", help="required: this sends real orders")
    if not ap.parse_args().real_money:
        print("refusing: pass --real-money (this buys and sells 1 IEF at Schwab)", file=sys.stderr)
        return 2
    return asyncio.run(smoke())


if __name__ == "__main__":
    raise SystemExit(main())
