"""Live spike-scanner experiment book (prereg_scanner_book_2026-11-09).

Owner 2026-11-09: more trades at lower risk, from stocks we monitor, not only the channel. The detector is the tested
self-spike rule (prereg_self_fade_2026-11-06, which FAILED its training gate at -1.23%/trade), so this book is a small
measurement: 0.5% of its own NAV per trade, its own table (it never blocks or sizes the channel fade), no per-trade
texts. Estimates use IEX; the official result is re-scored nightly on SIP bars (scripts/scan_settle.py).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from typing import Callable

import pandas as pd

from sgt.fast.fade import ET, PRICE_MAX, PRICE_MIN
from sgt.intraday.calendar import default_calendar
from sgt.ops.store import OpsStore, iso

log = logging.getLogger(__name__)
RUNUP, VOL_X, LOOKBACK, MIN_PRIOR, VOL_WINDOW = 0.05, 5.0, 5, 10, 20
FIRST_BAR, LAST_BAR = time(9, 35), time(15, 44)          # bar t CLOSES in this window (ET)
WEIGHT, NAV0, STOP, HOLD = 0.005, 100_000.0, 0.20, timedelta(minutes=60)
MAX_DAY, MAX_OPEN = 10_000, 40                         # owner 2026-11-09: no trade limit; runaway guard only
MAX_ENTRY_DELAY = timedelta(minutes=5)
_SYM = re.compile(r"^[A-Z]{1,5}$")

Quote = Callable[..., "float | None"]


def tradable_symbol(sym: str) -> bool:
    """1-5 capital letters; 5-letter symbols ending W/U/R are warrants, units or rights."""
    return bool(_SYM.match(sym or "")) and not (len(sym) == 5 and sym[-1] in "WUR")


def detect(bars: pd.DataFrame, now: datetime) -> dict | None:
    """The tested spike at the close of the last COMPLETE RTH bar (``ts`` = bar start, UTC), or None."""
    if bars.empty:
        return None
    b = bars[bars.ts + pd.Timedelta(minutes=1) <= pd.Timestamp(now)].sort_values("ts").reset_index(drop=True)
    et = b.ts.dt.tz_convert(ET)
    b = b[(et.dt.time >= time(9, 30)) & (et.dt.time < time(16, 0))].reset_index(drop=True)
    if len(b) < max(MIN_PRIOR, LOOKBACK) + 1:
        return None
    t = len(b) - 1
    last = b.iloc[t]
    close_et = (last.ts + pd.Timedelta(minutes=1)).tz_convert(ET).time()
    if not FIRST_BAR <= close_et <= LAST_BAR or not PRICE_MIN <= last.close <= PRICE_MAX:
        return None
    prior = b.iloc[max(0, t - VOL_WINDOW):t]
    if len(prior) < MIN_PRIOR or b.close.iloc[t - LOOKBACK] <= 0:
        return None
    runup = last.close >= (1 + RUNUP) * b.close.iloc[t - LOOKBACK]
    burst = last.volume >= VOL_X * prior.volume.mean() and prior.volume.mean() > 0
    new_high = last.high > b.high.iloc[:t].max()
    if runup and burst and new_high:
        return {"bar_ts": last.ts, "signal_px": float(last.close)}
    return None


def nav(db: OpsStore) -> float:
    with db.tx() as c:
        rows = c.execute("SELECT weight, net FROM scan_trades WHERE status='settled' ORDER BY settled_at, id").fetchall()
    v = NAV0
    for r in rows:
        v *= 1 + r["weight"] * r["net"]
    return v


def record(db: OpsStore, symbol: str, ev: dict, now: datetime) -> bool:
    """Insert a pending trade (first event per symbol-day; caps). True when inserted."""
    session = ev["bar_ts"].tz_convert(ET).date().isoformat()
    with db.tx() as c:
        n_day = c.execute("SELECT COUNT(*) FROM scan_trades WHERE session=? AND status!='void'", (session,)).fetchone()[0]
        n_open = c.execute("SELECT COUNT(*) FROM scan_trades WHERE status IN ('pending','open')").fetchone()[0]
        status, reason = ("skipped", "daily cap") if n_day >= MAX_DAY else \
            ("skipped", "runaway guard") if n_open >= MAX_OPEN else ("pending", "")
        cur = c.execute("INSERT OR IGNORE INTO scan_trades(symbol, session, bar_ts, detected_at, signal_px, status, reason,"
                        " weight, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                        (symbol, session, iso(ev["bar_ts"].to_pydatetime()), iso(now), ev["signal_px"], status, reason,
                         WEIGHT, iso(now)))
    return cur.rowcount == 1


def _set(db: OpsStore, rid: int, expect: str, **cols) -> bool:
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:
        return c.execute(f"UPDATE scan_trades SET {keys} WHERE id=? AND status=?",  # noqa: S608 - fixed column names
                         (*cols.values(), rid, expect)).rowcount == 1


def session_close(session: str) -> datetime:
    """The trade session's regular close from the exchange calendar (adversarial pass 1, F-9: was a fixed 16:00)."""
    return default_calendar().bounds(date.fromisoformat(session)).rth_close


def _say(send, text: str) -> None:
    if send is None:
        return
    try:
        send(text)
    except Exception:  # noqa: BLE001 - a text failure never stops the book; logged
        log.warning("scanner text failed")


def step(db: OpsStore, quote: Quote, now: datetime, send=None) -> None:
    """pending -> open at the first print in bar t+1 (within 5 min); open -> est_closed on the stop or 60 minutes.
    ``send``: owner 2026-11-09 wants a text for every new trade and every exit (experiment trades too)."""
    with db.tx() as c:
        rows = c.execute("SELECT * FROM scan_trades WHERE status IN ('pending','open') ORDER BY id").fetchall()
    for r in rows:
        try:
            bar = datetime.fromisoformat(r["bar_ts"])
            start = bar + timedelta(minutes=1)                   # the tested entry bar t+1
            close = session_close(r["session"])                  # F-9: the calendar's close (13:00 on half days)
            if r["status"] == "pending":
                if now < start:
                    continue
                if now >= close or start >= close:               # pass 2, F-12: never open on a post-close print
                    _set(db, r["id"], "pending", status="skipped", reason="session closed before entry")
                    continue
                px = quote(r["symbol"], since=start) if getattr(quote, "since_aware", False) else quote(r["symbol"])
                if px is None or not px > 0:
                    if now - start > MAX_ENTRY_DELAY:
                        _set(db, r["id"], "pending", status="skipped", reason="no print within 5 min")
                    continue
                if _set(db, r["id"], "pending", status="open", est_entry=px, est_last=px, est_entry_at=iso(now)):
                    out = min(now + HOLD, session_close(r["session"]))
                    _say(send, f"[SGT] 🔎 SCANNER SHORT {r['symbol']} @ ${px:.2f} · ${WEIGHT * nav(db):,.0f} (0.5%, "
                               f"experiment) · stop ${px * (1 + STOP):.2f} · out {out.astimezone(ET):%H:%M} ET\n"
                               f"Why: +5% in 5 min on 5x volume to a new high (spike bar {bar.astimezone(ET):%H:%M})\n"
                               f"https://www.tradingview.com/chart/?symbol={r['symbol']}")
                continue
            px = quote(r["symbol"]) if now < close else None   # after the close: the last session mark, not a late print
            entry = r["est_entry"]
            last = px if px and px > 0 else r["est_last"]
            out_at = min(datetime.fromisoformat(r["est_entry_at"]) + HOLD, close)
            closed = None
            if px and px >= entry * (1 + STOP):
                if _set(db, r["id"], "open", status="est_closed", reason="stop", est_last=px, est_exit=px,
                        est_exit_at=iso(now)):
                    closed = ("stop hit", px)
            elif now >= out_at:
                why = "60 min" if out_at < close else "close"
                if _set(db, r["id"], "open", status="est_closed", reason=why, est_last=last, est_exit=last,
                        est_exit_at=iso(now)):
                    closed = (why, last)
            if closed and closed[1]:
                ret = (entry - closed[1]) / entry
                _say(send, f"[SGT] 🔎 SCANNER OUT {r['symbol']} · {'made' if ret > 0 else 'lost'} {ret:+.1%} "
                           f"(${ret * r['weight'] * nav(db):+,.0f} est) · ${entry:.2f} -> ${closed[1]:.2f} · {closed[0]}")
            elif px and px > 0 and not closed:
                _set(db, r["id"], "open", est_last=px)
        except Exception:  # noqa: BLE001 - one bad row must not stall the book; logged with its id
            log.exception("scan row %s failed", r["id"])


def due(db: OpsStore, now: datetime, after: timedelta = timedelta(minutes=80)) -> pd.DataFrame:
    """Trades whose official 60-minute window is final on SIP."""
    with db.tx() as c:
        rows = c.execute("SELECT * FROM scan_trades WHERE status IN ('open','est_closed') AND bar_ts <= ?",
                         (iso(now - after),)).fetchall()
    return pd.DataFrame([dict(r) for r in rows])


def book(db: OpsStore, rid: int, expect: str, res: dict | None, now: datetime) -> bool:
    """Write one official result (None -> void: no SIP entry bar)."""
    if res is None:
        return _set(db, rid, expect, status="void", reason="no SIP entry bar", settled_at=iso(now))
    return _set(db, rid, expect, status="settled", settled_at=iso(now), **res)


def summary(db: OpsStore, session: str) -> str | None:
    """One line for the daily digest (owner: no per-trade texts for the experiment)."""
    with db.tx() as c:
        today = c.execute("SELECT status, net FROM scan_trades WHERE session=?", (session,)).fetchall()
        allr = c.execute("SELECT net FROM scan_trades WHERE status='settled'").fetchall()
    if not today and not allr:
        return None
    s = [r["net"] for r in today if r["status"] == "settled" and r["net"] is not None]
    nets = [r["net"] for r in allr if r["net"] is not None]
    line = f"scanner (experiment, 0.5%/trade): today {len(today)} signals"
    if s:
        line += f", {len(s)} settled avg {sum(s) / len(s):+.1%}"
    if nets:
        line += f"; all {len(nets)} avg {sum(nets) / len(nets):+.2%}, book ${nav(db):,.0f}"
    return line + " (tested -1.2%/trade; decide at 300)"
