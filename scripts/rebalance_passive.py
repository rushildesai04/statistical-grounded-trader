"""Monthly passive rebalance (default 60% SPY / 40% IEF) on Alpaca. Safe by default.

* DRY RUN unless --execute; PAPER unless --live --confirm-live.
* Acts only during 09:45-15:55 ET on the first 5 trading days of the month (unless --force); the
  schedule retries hourly and at wake, so a closed laptop just delays the rebalance (2026-10-30).
* Idempotent: client order ids are rebal-YYYYMM-SYMBOL-SIDE; if any order with this month's
  prefix exists at the broker, the run exits without trading.
* Sells first; waits for them to finish; re-reads the account; then buys from actual cash.
* SMS approval (2026-11-02): with --execute, a non-empty plan is texted to the owner and orders are
  submitted only by the run the poller starts after "OK <code>" (scripts/sms_control.py), and only
  if the fresh plan still matches what was approved. --no-approval skips this for manual runs.
* Limit orders 0.3% through the latest trade; per-order notional cap; manages only the target
  symbols (other holdings are never touched). Any data/broker error stops the run.

Usage:
    python scripts/rebalance_passive.py                 # dry-run plan
    python scripts/rebalance_passive.py --execute       # paper, on rebalance day only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from sgt.allocation.rebalance import DEFAULT_TARGETS, in_rebalance_days, plan
from sgt.brokers.alpaca import AlpacaBroker
from sgt.config import get_settings
from sgt.core.types import OrderIntent, OrderStatus, OrderType, Side, TimeInForce
from sgt.db.session import init_db
from sgt.intraday.sources import _alpaca_headers
from sgt.ops import approvals
from sgt.ops.sms import Sms, notify
from sgt.ops.store import OpsStore

ET = ZoneInfo("America/New_York")
WINDOW = (dtime(9, 45), dtime(15, 55))
STALE_S = 300                                   # a price this old cannot set a limit order
VERY_STALE_S = 3600                             # ...or even measure drift (2026-11-06: IEX prints for IEF can be 5+ min
                                                # apart midday; the 300 s guard alone failed runs that needed no trade)
STRATEGY = "passive_60_40"


def latest_prices(symbols: list[str]) -> dict[str, float]:
    s = get_settings()
    r = httpx.get(s.alpaca_data_url.rstrip("/") + "/v2/stocks/trades/latest",
                  params={"symbols": ",".join(symbols), "feed": "iex"},
                  headers=_alpaca_headers(), timeout=s.http_timeout_s)
    r.raise_for_status()
    trades = r.json().get("trades") or {}
    now = datetime.now(timezone.utc)
    out = {}
    for sym in symbols:
        t = trades.get(sym)
        if not isinstance(t, dict) or not float(t.get("p", 0)) > 0:
            raise SystemExit(f"no latest trade for {sym}; refusing")
        age = (now - datetime.fromisoformat(str(t["t"]).replace("Z", "+00:00"))).total_seconds()
        out[sym] = (float(t["p"]), age)
    return out


async def month_already_done(broker: AlpacaBroker, prefix: str, after: datetime) -> bool:
    resp = await broker._request("GET", "/v2/orders", retry=True,
                                 params={"status": "all", "after": after.isoformat(), "limit": 500})
    broker._raise_for_status(resp, "orders")
    return any(str(o.get("client_order_id", "")).startswith(prefix) for o in resp.json())


def intent(account_id: str, prefix: str, o, now: datetime) -> OrderIntent:
    return OrderIntent(client_order_id=f"{prefix}{o.symbol}-{o.side}", account_id=account_id,
                       symbol=o.symbol, side=Side(o.side), qty=o.qty, order_type=OrderType.LIMIT,
                       limit_price=o.limit, tif=TimeInForce.DAY, extended_hours=False,
                       strategy_id="passive_60_40", reason="rebalance", created_at=now)


async def wait_terminal(broker: AlpacaBroker, cids: list[str], timeout_s: float = 180) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        acks = [await broker._lookup(c) for c in cids]
        if all(a is not None and a.status in (OrderStatus.FILLED, OrderStatus.CANCELED,
                                              OrderStatus.REJECTED, OrderStatus.EXPIRED) for a in acks):
            return
        await asyncio.sleep(5)
    raise SystemExit("sell orders not terminal within timeout; buys skipped (rerun is idempotent)")


async def main_async(args) -> int:
    now = datetime.now(timezone.utc)
    et = now.astimezone(ET)
    if not args.force and not (in_rebalance_days(et.date()) and WINDOW[0] <= et.time() <= WINDOW[1]):
        print(f"not rebalance time ({et:%Y-%m-%d %H:%M} ET); nothing to do")
        return 0
    targets = dict(DEFAULT_TARGETS) if not args.targets else {
        k: float(v) for k, v in (kv.split("=") for kv in args.targets.split(","))}
    prefix = f"rebal-{et:%Y%m}-"
    broker = AlpacaBroker(args.account_id, paper=not args.live)
    approval_id = None
    try:
        await broker.connect()
        month_start = et.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
        if await month_already_done(broker, prefix, month_start):
            print(f"orders with prefix {prefix} already exist; already rebalanced this month")
            return 0
        px_age = latest_prices(list(targets))
        stale = {s: round(a) for s, (_, a) in px_age.items() if a > STALE_S}
        if args.execute and any(a > VERY_STALE_S for a in stale.values()):
            raise SystemExit(f"stale prices (seconds since the last trade) {stale}; refusing to trade")
        prices = {s: p for s, (p, _) in px_age.items()}

        def current_plan(state):
            holdings = {p.symbol: p.qty for p in state.positions}
            try:
                return plan(cash=state.cash, holdings=holdings, prices=prices, targets=targets,
                            max_order_notional=args.max_order_notional)
            except ValueError as exc:      # fail closed with a readable reason, no trades
                raise SystemExit(f"refusing to trade: {exc}") from exc

        state = await broker.account_state()
        orders = current_plan(state)
        record = {"run_at": now.isoformat(), "mode": "live" if args.live else "paper",
                  "execute": bool(args.execute), "prices": prices, "cash": state.cash,
                  "holdings": {p.symbol: p.qty for p in state.positions},
                  "plan": [asdict(o) for o in orders]}
        for o in orders:
            print(f"PLAN {o.side:4s} {o.qty:6d} {o.symbol:4s} @ {o.limit:.2f}  (${o.notional:,.0f})")
        if not orders:
            print("within drift threshold; no trades" + (f" (prices {stale} s old, fine for drift)" if stale else ""))
        elif stale and args.execute:                    # an order needs a fresh price for its limit
            raise SystemExit(f"stale prices (seconds since the last trade) {stale}; refusing to trade")
        if args.execute and orders and not args.no_approval:
            approval_id = approval_gate(args, orders, et, now)
            if approval_id is None:
                return 0
        submitted = []
        if args.execute and orders:
            sells = [o for o in orders if o.side == "sell"]
            for o in sells:
                print("submit", await broker.submit(intent(args.account_id, prefix, o, now)))
                submitted.append(o)
            buys = [o for o in orders if o.side == "buy"]
            if sells:
                await wait_terminal(broker, [f"{prefix}{o.symbol}-sell" for o in sells])
                state = await broker.account_state()
                buys = [o for o in current_plan(state) if o.side == "buy"]
                if approval_id is not None:             # re-planned buys must still match what was approved
                    approved_buys = [o for o in json.loads(approvals.get(OpsStore(), approval_id)["payload"])["plan"]
                                     if o["side"] == "buy"]
                    if not same_plan(approved_buys, buys):
                        raise SystemExit("re-planned buys differ from the approved plan; buys skipped: " + plan_text(buys))
            for o in buys:
                print("submit", await broker.submit(intent(args.account_id, prefix, o, now)))
                submitted.append(o)
        out = Path("reports/rebalance") / f"{et:%Y-%m}{'' if args.execute else '-dryrun'}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, indent=2, default=str))
        if approval_id is not None:
            msg = "60/40 submitted: " + "; ".join(f"{o.side.upper()} {o.qty} {o.symbol} @ {o.limit:.2f}" for o in submitted)
            approvals.finish(OpsStore(), approval_id, True, msg)
            notify(f"[SGT] {msg}")
        return 0
    except BaseException as exc:
        if approval_id is not None:
            approvals.finish(OpsStore(), approval_id, False, f"{type(exc).__name__}: {str(exc)[:200]}")
        raise
    finally:
        await broker.close()


def plan_text(orders) -> str:
    return "; ".join(f"{o.side.upper()} {o.qty} {o.symbol} @ {o.limit:.2f} (${o.notional:,.0f})" for o in orders)


def same_plan(approved: list[dict], orders, tol: float = 0.10) -> bool:
    """Same symbols and sides, each quantity within max(2 shares, 10%) of what was approved."""
    a = {(o["symbol"], o["side"]): o["qty"] for o in approved}
    b = {(o.symbol, o.side): o.qty for o in orders}
    return a.keys() == b.keys() and all(abs(b[k] - a[k]) <= max(2, tol * a[k]) for k in a)


def approval_gate(args, orders, et: datetime, now: datetime) -> int | None:
    """Returns the claimed approval id when this run may submit; otherwise requests/awaits approval."""
    db = OpsStore()
    period = f"{et:%Y-%m}"
    if db.paused():
        print("trading PAUSED by SMS; no request sent")
        return None
    if args.approval_id is not None:
        row = approvals.latest(db, STRATEGY, period)
        if row is None or row["id"] != args.approval_id or not approvals.claim(db, args.approval_id, now):
            raise SystemExit(f"approval #{args.approval_id} is not the approved request for {period}; refusing")
        if same_plan(json.loads(row["payload"])["plan"], orders):
            return args.approval_id
        approvals.finish(db, args.approval_id, False, "plan changed since approval: " + plan_text(orders))
        print("plan changed since approval; asking again")
    else:
        row = approvals.latest(db, STRATEGY, period)
        if row is not None and row["status"] in ("pending", "approved", "executing", "executed", "rejected"):
            print(f"approval #{row['id']} for {period} is {row['status']}; not trading in this run")
            return None
    close = datetime.combine(et.date(), WINDOW[1], ET)
    aid = approvals.request(db, sms_send, strategy=STRATEGY, kind="broker", period=period,
                            summary=f"60/40 paper rebalance: {plan_text(orders)}",
                            payload={"plan": [asdict(o) for o in orders]}, expires_at=close, now=now)
    print(f"approval requested (#{aid}); orders wait for OK <code>")
    return None


def sms_send(text: str) -> None:
    sms = Sms()
    try:
        sms.send(text)
    finally:
        sms.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--account-id", default="alpaca_paper_main")
    ap.add_argument("--execute", action="store_true", help="submit orders (default: dry run)")
    ap.add_argument("--live", action="store_true", help="use the LIVE Alpaca account")
    ap.add_argument("--confirm-live", action="store_true", help="required with --live")
    ap.add_argument("--force", action="store_true", help="ignore the rebalance-day/time window")
    ap.add_argument("--targets", default="", help="e.g. SPY=0.6,IEF=0.4 (must sum to 1)")
    ap.add_argument("--max-order-notional", type=float, default=50_000.0)
    ap.add_argument("--approval-id", type=int, default=None, help="execute this SMS-approved request (poller)")
    ap.add_argument("--no-approval", action="store_true", help="owner at the terminal: skip the SMS approval")
    args = ap.parse_args(argv)
    if args.live and not args.confirm_live:
        raise SystemExit("--live requires --confirm-live")
    if args.live and args.force and args.execute:
        raise SystemExit("--force is not allowed with a live --execute")
    init_db()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
