"""Real-money alert-fade shorts at Schwab, mirroring the live fade book (design live_pilot_schwab_2026-11-05).

Called every listener tick after ``fade.monitor``. Stateless: each trade's progress is read back from the pilot
ledger by deterministic order ids ``pilot:fade:<fade id>:<leg>``, so a restart resumes exactly where it stopped.

Per fade trade the virtual book opened (F5: enter at the tested time; NO = exit):
1. entry   SELL_SHORT limit 0.2% through the quote, only within MAX_LATE of the virtual entry, only when the owner
           ARMed today (the daily OK for real shorts), real fades are on, no PDT block and no real day-loss stop.
           Whatever is unfilled after ENTRY_WAIT is canceled; Schwab rejecting the short = not borrowable, logged.
2. stop    broker-held BUY_TO_COVER STOP at +20% of the fill as soon as shares are short (F-3): protects the
           position even if this server or the login dies.
3. cover   when the virtual trade exits (stop, 60 min, close or the owner's NO) or the close is near: cancel the
           stop, then cover1 (+0.2%), cover2 (+1%), covermkt, STEP apart; inside CLOSE_GUARD straight to market.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Awaitable, Callable
from datetime import date, datetime, timedelta
from typing import Any

from sgt.fast import fade
from sgt.intraday.calendar import default_calendar
from sgt.ops import approvals
from sgt.ops.sms_style import bold, italic
from sgt.ops.store import OpsStore, iso, utcnow
from sgt.pilot import ledger
from sgt.pilot.executor import Pilot, tick
from sgt.pilot.ledger import CapRefused

log = logging.getLogger(__name__)

LIVE_FLAG = "pilot_fade_live"             # "1" = real fade shorts on (owner command); anything else = paper only
SIZE_FLAG = "pilot_fade_size"             # dollars per trade
DAY_LOSS_FLAG = "pilot_fade_day_loss"     # realized real loss (dollars) that stops new real entries for the day
DEFAULT_SIZE, MAX_SIZE, DEFAULT_DAY_LOSS = 500.0, 2_000.0, 400.0
MAX_LATE = timedelta(minutes=2)           # the edge is front-loaded (challengers: +2 min delay keeps ~15%)
ENTRY_WAIT = timedelta(seconds=60)
STEP = timedelta(seconds=20)
CLOSE_GUARD = timedelta(minutes=3)
THROUGH = 0.002
COVERS = (("cover1", 0.002), ("cover2", 0.01), ("covermkt", None))
PDT_EQUITY = 25_000.0

Quote = Callable[[str], "float | None"]
Flags = Callable[[], Awaitable[dict[str, Any]]]


def _f(db: OpsStore, key: str, default: float, hi: float | None = None) -> float:
    try:
        v = float(db.flag(key) or default)
    except ValueError:
        return default
    if not math.isfinite(v) or v <= 0:
        return default
    return min(v, hi) if hi else v


def realized(db: OpsStore, session: str) -> float:
    """Real fade P&L (dollars, before fees) of trades that started in ``session``, from actual fills."""
    pnl = 0.0
    with db.tx() as c:
        rows = c.execute("SELECT o.client_order_id, o.side, o.filled_qty, o.avg_price FROM pilot_orders o JOIN "
                         "fade_trades f ON o.client_order_id LIKE 'pilot:fade:' || f.id || ':%' WHERE f.session=? "
                         "AND o.filled_qty > 0", (session,)).fetchall()
    for r in rows:
        pnl += (1 if r["side"] == "SELL_SHORT" else -1) * r["filled_qty"] * r["avg_price"]
    # an open short is worth -qty * price; count it at entry price (0 P&L) so only closed results stop the day
    for p in ledger.positions(db, "fade"):
        with db.tx() as c:
            hit = c.execute("SELECT 1 FROM fade_trades WHERE session=? AND symbol=?", (session, p.symbol)).fetchone()
        if hit:
            pnl += p.qty * p.cost
    return pnl


def pdt_block(flags: dict[str, Any]) -> str | None:
    """F-2: None = entry allowed. Unknown facts block (fail closed); FINRA's PDT removal (eff. 2026-06-04) may be
    phased in by brokers until 2027-10-20, so an under-$25k account flagged at 3 round trips is still respected."""
    if flags.get("closing_only") is True:
        return "Schwab has the account on closing-only"
    if flags.get("closing_only") is None or flags.get("equity") is None:
        return "Schwab day-trade status unreadable"
    try:
        equity = float(flags["equity"])
    except (TypeError, ValueError):
        return "Schwab equity unreadable"
    if equity >= PDT_EQUITY or flags.get("day_trader") is True:
        return None
    try:
        trips = int(flags["round_trips"])
    except (KeyError, TypeError, ValueError):
        return "Schwab round-trip count unreadable"
    return "3 day trades in 5 days on an account under $25k" if trips >= 3 else None


async def step(pilot: Pilot, db: OpsStore, quote: Quote, send, account_flags: Flags,
               now: datetime | None = None) -> None:
    now = now or utcnow()
    session = now.astimezone(fade.ET).date().isoformat()
    for note in await pilot.sync():
        log.warning("pilot sync: %s", note)
    with db.tx() as c:
        rows = c.execute("SELECT * FROM fade_trades WHERE session=? AND est_entry_at IS NOT NULL ORDER BY id",
                         (session,)).fetchall()
    for r in rows:
        try:
            await _trade(pilot, db, quote, send, account_flags, r, now)
        except CapRefused as e:
            log.info("fade %s real: %s", r["id"], e)
        except Exception:  # noqa: BLE001 - one trade must not stall the others; the next tick retries
            log.exception("fade %s real step failed", r["id"])


def _marks(db: OpsStore, quote: Quote, symbol: str, px: float) -> dict[str, float]:
    """Live marks for every pilot position (the total cap values them all) plus this symbol. A missing quote is
    left out, so the cap check refuses the entry (fail closed). Review 2026-11-05 L2: passing only the new
    symbol refused every real fade while 60/40 was held."""
    out = {symbol: px}
    for sym in {p.symbol for p in ledger.positions(db)} - {symbol}:
        q = quote(sym)
        if q is not None and math.isfinite(q) and q > 0:
            out[sym] = q
    return out


def _oid(rid: int, leg: str) -> str:
    return ledger.order_id("fade", str(rid), leg)


def _open(row) -> bool:
    return row is not None and row["status"] in ledger.OPEN_STATES


def _age(row, now: datetime) -> timedelta:
    return now - datetime.fromisoformat(row["created_at"])


async def _trade(pilot: Pilot, db: OpsStore, quote: Quote, send, account_flags: Flags, r, now: datetime) -> None:
    rid, sym = r["id"], r["symbol"]
    entry = ledger.get(db, _oid(rid, "entry"))
    if entry is None:
        await _maybe_enter(pilot, db, quote, send, account_flags, r, now)
        return
    if _open(entry):
        if _age(entry, now) >= ENTRY_WAIT:
            await pilot.cancel(entry["client_order_id"])
        return
    short = -sum(p.qty for p in ledger.positions(db, "fade") if p.symbol == sym)
    close = default_calendar().bounds(date.fromisoformat(r["session"])).rth_close
    started = datetime.fromisoformat(entry["created_at"])
    out = r["status"] != "open" or now >= fade._exit_at(r["session"], started)[0] or now >= close - CLOSE_GUARD
    stop = ledger.get(db, _oid(rid, "stop"))
    if short <= 0:
        _report(db, send, rid, sym, entry)
        return
    if not out:
        if stop is None:
            px = tick(entry["avg_price"] * (1 + fade.STOP), up=True)
            await pilot.place(strategy="fade", decision=str(rid), leg="stop", symbol=sym, side="BUY_TO_COVER",
                              qty=short, marks={}, stop_price=px)
            send(f"[SGT] 🛡 {bold('REAL')} {bold(sym)}: Schwab holds a stop to cover {short} at ${fade._fmt(px)}")
        return
    if _open(stop):
        await pilot.cancel(stop["client_order_id"])
        return                                            # next tick: the stop may have filled meanwhile
    await _cover(pilot, db, quote, send, rid, sym, short, now, near_close=now >= close - CLOSE_GUARD)


async def _maybe_enter(pilot: Pilot, db: OpsStore, quote: Quote, send, account_flags: Flags, r,
                       now: datetime) -> None:
    if r["status"] != "open" or db.flag(LIVE_FLAG) != "1" or not approvals.armed(db, now):
        return
    if db.flag(f"pilot_fade_skip:{r['id']}"):
        return
    if now - datetime.fromisoformat(r["est_entry_at"]) > MAX_LATE:
        return
    close = default_calendar().bounds(date.fromisoformat(r["session"])).rth_close
    if now >= close - 2 * CLOSE_GUARD:                    # tested rule enters until 15:44 and covers at the close;
        return                                            # the last minutes leave no room to work a cover
    loss = _f(db, DAY_LOSS_FLAG, DEFAULT_DAY_LOSS)
    if realized(db, r["session"]) <= -loss:
        _once(db, f"pilot_fade_daystop:{r['session']}", send,
              f"[SGT] ⛔ {bold('REAL fades stopped for today')}: real loss reached ${loss:,.0f}. Paper book continues.")
        return
    block = pdt_block(await account_flags())
    if block:
        _once(db, f"pilot_fade_pdt:{r['session']}:{block}", send,
              f"[SGT] ⛔ {bold('REAL fade skipped')}: {block}. Paper book continues.")
        return
    px = quote(r["symbol"])
    if px is None or not math.isfinite(px) or px <= 0:
        return
    size = _f(db, SIZE_FLAG, DEFAULT_SIZE, MAX_SIZE)
    limit = tick(px * (1 - THROUGH), up=False)
    qty = int(size // px)
    if qty < 1:
        return
    try:
        await pilot.place(strategy="fade", decision=str(r["id"]), leg="entry", symbol=r["symbol"], side="SELL_SHORT",
                          qty=qty, marks=_marks(db, quote, r["symbol"], px), limit_price=limit)
    except CapRefused as e:                               # no ledger row: text once, never retry this trade
        _once(db, f"pilot_fade_skip:{r['id']}", send,
              f"[SGT] ⚠️ {bold('REAL short not placed')} {bold(r['symbol'])}: {str(e)[:120]}. Paper book continues.")
        return
    send(f"[SGT] 💵 {bold('REAL')} short {qty} {bold(r['symbol'])} limit ${fade._fmt(limit)} "
         f"(${qty * limit:,.0f}) sent to Schwab\n" + italic("Unfilled part cancels in 60 s; stop follows the fill."))


async def _cover(pilot: Pilot, db: OpsStore, quote: Quote, send, rid: int, sym: str, short: int, now: datetime,
                 near_close: bool) -> None:
    legs = COVERS[-1:] if near_close else COVERS
    for leg, through in COVERS:
        row = ledger.get(db, _oid(rid, leg))
        if _open(row):
            if leg == "covermkt":                         # never cancel the last resort; a halt keeps it working
                if _age(row, now) >= 4 * STEP:
                    _once(db, f"pilot_fade_stuck:{rid}", send,
                          f"[SGT] 🚨 {bold('REAL')} {bold(sym)}: market cover for {short} not filled yet "
                          "(halt?). It stays working; check the Schwab app.")
            elif near_close or _age(row, now) >= STEP:
                await pilot.cancel(row["client_order_id"])
            return
        if row is not None or (leg, through) not in legs:
            continue                                      # finished (filled part already counted) or skipped
        px = quote(sym)
        if through is not None and (px is None or not math.isfinite(px) or px <= 0):
            continue                                      # no quote: go straight to the market cover
        await pilot.place(strategy="fade", decision=str(rid), leg=leg, symbol=sym, side="BUY_TO_COVER", qty=short,
                          marks={sym: px} if px else {}, limit_price=tick(px * (1 + through), up=True) if through
                          is not None else None)
        return
    _once(db, f"pilot_fade_stuck:{rid}", send,
          f"[SGT] 🚨 {bold('REAL')} {bold(sym)}: still short {short} after every cover try. "
          f"Text FLATTEN or cover it in the Schwab app.")


def _report(db: OpsStore, send, rid: int, sym: str, entry) -> None:
    if not entry["filled_qty"]:
        if entry["status"] in ("rejected", "canceled", "expired"):
            why = "not borrowable / rejected at Schwab" if entry["status"] == "rejected" else "did not fill"
            _once(db, f"pilot_fade_done:{rid}", send, f"[SGT] ℹ️ {bold('REAL')} {bold(sym)}: {why}; no position.")
        return
    with db.tx() as c:
        legs = c.execute("SELECT side, filled_qty, avg_price FROM pilot_orders WHERE client_order_id LIKE ? AND "
                         "filled_qty > 0", (f"pilot:fade:{rid}:%",)).fetchall()
    pnl = sum((1 if g["side"] == "SELL_SHORT" else -1) * g["filled_qty"] * g["avg_price"] for g in legs)
    basis = entry["filled_qty"] * entry["avg_price"]
    covered = [g for g in legs if g["side"] == "BUY_TO_COVER"]
    out_px = sum(g["filled_qty"] * g["avg_price"] for g in covered) / max(sum(g["filled_qty"] for g in covered), 1)
    mark = "🟢" if pnl > 0 else "🔴"
    _once(db, f"pilot_fade_done:{rid}", send,
          f"[SGT] {mark} {bold('REAL OUT')} {bold(sym)}: short {entry['filled_qty']} @ ${fade._fmt(entry['avg_price'])}"
          f" → ${fade._fmt(out_px)}\nP&L {bold(f'${pnl:+,.2f} ({pnl / basis:+.1%})')} before fees")


def _once(db: OpsStore, key: str, send, text: str) -> None:
    """Send a text at most once per key (flags table, atomic insert)."""
    with db.tx() as c:
        new = c.execute("INSERT OR IGNORE INTO flags(key, value, updated_at) VALUES (?, '1', ?)",
                        (key, iso(utcnow()))).rowcount == 1
    if new:
        send(text)
