"""The trader's rule, tracked beside every long (owner 2026-11-18: "take the trader's advice to heart").

"As long as the 1M shows all green, stay in the trade ... MACD going red is usually a sign to start exiting."
For every long of the day, traded or watched, also after the book's own exit: the moment MACD turns red after it
has been green since the entry (``trend_matrix.going_red``), or the session's end, is recorded once
(``long_trades.tr_exit_*``) and texted, so each day shows the book's exit next to his. Nothing here is read by a
book: fills, exits and totals stay the book's. Prices are the free feed's candles: estimates, before costs.
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from sgt.fast import long_book, trend_matrix
from sgt.fast import patterns as pt
from sgt.fast.fade import ET
from sgt.ops.store import OpsStore, iso

log = logging.getLogger(__name__)
STILL_IN = "still in at the close"
# 2026-11-19: the first deploy of this module (23:09 ET) texted four "trader's rule" messages about longs that had
# closed hours earlier. An exit found long after it happened is recorded but not texted.
FRESH = pd.Timedelta(minutes=30)
EVERY_S, MAX_ROWS = 55, 40                # candles close once a minute: one look a minute per long is enough
_last: dict[str, datetime] = {}           # per store: when the last look was made (the book passes every 15 s)


def track(db: OpsStore, bars: long_book.LiveBars, send, now: datetime) -> int:
    """Record, once per long of today, where the trader's rule leaves it; text it when ``send`` is given and the
    exit is at most FRESH old (a restart hours later records it without a text)."""
    key = str(db.path)
    if key in _last and (now - _last[key]).total_seconds() < EVERY_S:
        return 0
    _last[key] = now
    session = now.astimezone(ET).date().isoformat()
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, symbol, session, status, entry_ts, est_entry, est_exit FROM long_trades WHERE session=? AND "
            "entry_ts IS NOT NULL AND est_entry IS NOT NULL AND tr_exit_at IS NULL AND status IN ('open','est_closed',"
            "'settled',?) ORDER BY id LIMIT ?", (session, long_book.SEEN, MAX_ROWS))]
    if not rows:
        return 0
    close = pd.Timestamp(long_book.default_close(session))
    over = pd.Timestamp(now) >= close + pt.BAR
    done = 0
    for r in rows:
        try:
            raw = bars(r["symbol"], now)
            x = trend_matrix.going_red(raw, pd.Timestamp(r["entry_ts"]), now)
            if x is None and over:                         # the session ended with his rule still in: the last price
                px = trend_matrix.last_price(raw, now)
                x = None if px is None else {"px": px, "at": close, "why": STILL_IN}
            if x is None:
                continue
            with db.tx() as c:
                ok = c.execute("UPDATE long_trades SET tr_exit_px=?, tr_exit_at=?, tr_why=? WHERE id=? AND tr_exit_at IS NULL",
                               (x["px"], iso(x["at"].to_pydatetime()), x["why"], r["id"])).rowcount == 1
            if ok:
                done += 1
                if send is not None and pd.Timestamp(now) - pd.Timestamp(x["at"]) <= FRESH:
                    send(text(r, x))
        except Exception:  # noqa: BLE001 - one trade failing never blocks the others; logged, tried again next pass
            log.exception("trader's rule for long %s failed", r["id"])
    return done


def text(r: dict, x: dict) -> str:
    entry = float(r["est_entry"])
    his = x["px"] / entry - 1
    book = "" if r.get("est_exit") is None else (f" Book's own exit: ${float(r['est_exit']):.2f} "
                                                 f"({float(r['est_exit']) / entry - 1:+.1%}).")
    tag = "watched, not traded" if r["status"] == long_book.SEEN else "paper"
    return (f"[SGT] 📐 TREND HOLD {r['symbol']} ({tag}): {x['why']} at {x['at'].tz_convert(ET):%H:%M} ET. Holding until then: about "
            f"${x['px']:.2f}, {his:+.1%} from the ${entry:.2f} entry, before costs.{book}")


def line(db: OpsStore, session: str) -> str | None:
    """Digest line: the book's exit next to the trader's rule for each long of the day (estimates, before costs)."""
    with db.tx() as c:
        rows = c.execute("SELECT symbol, est_entry, est_exit, tr_exit_px FROM long_trades WHERE session=? AND tr_exit_px IS "
                         "NOT NULL AND est_entry IS NOT NULL ORDER BY id", (session,)).fetchall()
    if not rows:
        return None
    bits = []
    for r in rows:
        book = "open" if r["est_exit"] is None else f"{r['est_exit'] / r['est_entry'] - 1:+.1%}"
        bits.append(f"{r['symbol']} book {book} / held {r['tr_exit_px'] / r['est_entry'] - 1:+.1%}")
    return "longs held until MACD turned, no stop (a measurement, before costs): " + ", ".join(bits)
