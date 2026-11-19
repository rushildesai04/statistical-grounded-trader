"""What price existed when a long text went out (owner 2026-11-18, option 1: "record the real price at text time").

The long book books a fill at the price of the fill bar and texts once that bar has closed, so the booked entry is a
price from about a minute before the phone buzzed. Nightly, from OFFICIAL prints (free once 15 minutes old):
- ``long_trades.text_px``: the first print at or after the fill text (``created_at``), and what buying THERE made with
  the booked stop and target levels (``text_how`` / ``text_gross``): stop or target first on the prints of the rest of
  that minute, then on the official 1-minute bars, else the close after HOLD. No red-candle exit: this is the plain
  order a reader of the text could place.
- ``long_armed.px``: the first print at or after an armed text (``sgt.fast.long_armed``).
A measurement, never part of a book value or a total. External bytes: the caller's ``Prints`` returns cleaned rows or
None on a failed request (retried the next night, for LOOKBACK_DAYS); rows per run are bounded.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

import numpy as np
import pandas as pd

from sgt.fast import long_book
from sgt.fast import patterns as pt
from sgt.fast.fade import ET
from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
Prints = Callable[[str, datetime, datetime], "np.ndarray | None"]   # (symbol, start, end) -> rows (ns, price, size)
WINDOW = timedelta(seconds=120)          # a text with no print this soon after it has no price
LOOKBACK_DAYS, MAX_ROWS = 14, 60
THROUGH_STOP, PAST_TARGET, NO_PRINT, NOT_LIVE = ("already through the stop", "already past the target",
                                                 "no print within 2 minutes", "not texted live")


def first_print(tr: np.ndarray | None, at: datetime) -> tuple[pd.Timestamp, float, np.ndarray] | None:
    """(time, price, the later prints of the same minute) of the first print at or after ``at`` within WINDOW."""
    if tr is None or not len(tr):
        return None
    t0 = pd.Timestamp(at).value
    i = int(np.searchsorted(tr[:, 0], t0, side="left"))
    if i >= len(tr) or tr[i, 0] > t0 + int(WINDOW.total_seconds() * 1e9) or not tr[i, 1] > 0:
        return None
    ts = pd.Timestamp(int(tr[i, 0]), tz="UTC").floor("us")       # prints are stamped in ns; a datetime holds us
    rest = tr[i + 1:]
    return ts, float(tr[i, 1]), rest[rest[:, 0] < (ts.floor("min") + pt.BAR).value]


def outcome(px: float, rest: np.ndarray, later: pd.DataFrame, stop: float, target: float) -> tuple[str, float | None]:
    """Buying at ``px`` with the booked stop and target: (how it ended, gross). No trade when ``px`` is beyond either."""
    if px <= stop:
        return THROUGH_STOP, None
    if px >= target:
        return PAST_TARGET, None
    for p in rest[:, 1] if len(rest) else ():
        if p <= stop:
            return "stop", stop / px - 1
        if p >= target:
            return "target", target / px - 1
    for b in later.itertuples():
        if b.low <= stop:
            return "stop", min(stop, b.open) / px - 1
        if b.high >= target:
            return "target", max(target, b.open) / px - 1
    last = float(later.close.iloc[-1]) if len(later) else (float(rest[-1, 1]) if len(rest) else px)
    return "time", last / px - 1


def run(db: OpsStore, prints: Prints, bars: long_book.SipBars, now: datetime | None = None) -> list[str]:
    """Price every long text not priced yet. Returns one line per row priced."""
    now = now or utcnow()
    since = (now.astimezone(ET).date() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    with db.tx() as c:
        trades = [dict(r) for r in c.execute(
            "SELECT id, symbol, session, stop, target, entry_ts, est_entry, entry, created_at FROM long_trades WHERE "
            "entry_ts IS NOT NULL AND text_how IS NULL AND status IN ('settled', ?) AND stop IS NOT NULL AND target IS NOT "
            "NULL AND session>=? ORDER BY id LIMIT ?", (long_book.SEEN, since, MAX_ROWS))]
        armed = [dict(r) for r in c.execute(
            "SELECT id, symbol, session, texted_at FROM long_armed WHERE texted_at IS NOT NULL AND px IS NULL AND px_note "
            "IS NULL AND session>=? ORDER BY id LIMIT ?", (since, MAX_ROWS))]
    out = []
    for r in trades + armed:
        try:
            if pd.Timestamp(now) < pd.Timestamp(long_book.default_close(r["session"])) + long_book.SIP_DELAY:
                continue                               # the session's official data is not complete yet
            line = _armed(db, r, prints) if "texted_at" in r else _trade(db, r, prints, bars)
        except Exception:  # noqa: BLE001 - one row failing never blocks the others; logged, retried next run
            log.exception("text price for %s %s failed", r["symbol"], r["id"])
            continue
        if line:
            out.append(line)
    return out


def _fetch(prints: Prints, symbol: str, at: datetime):
    """(the first print after ``at``, True) or (None, False) when the request failed (retry) / (None, True) no print."""
    end = (pd.Timestamp(at) + WINDOW).ceil("min").to_pydatetime()
    tr = prints(symbol, at, end)
    return (None, False) if tr is None else (first_print(tr, at), True)


def _trade(db: OpsStore, r: dict, prints: Prints, bars: long_book.SipBars) -> str | None:
    sent, entry_ts = pd.Timestamp(r["created_at"]), pd.Timestamp(r["entry_ts"])
    if sent - entry_ts > long_book.LIVE_LAG:           # booked long after the fact (restart): no live text to price
        _put(db, "long_trades", r["id"], "text_how", text_how=NOT_LIVE)
        return None
    hit, ok = _fetch(prints, r["symbol"], sent.to_pydatetime())
    if not ok:
        return None
    if hit is None:
        _put(db, "long_trades", r["id"], "text_how", text_how=NO_PRINT)
        return None
    ts, px, rest = hit
    f = bars(r["symbol"], r["session"])
    later = f[(f.ts > ts.floor("min")) & (f.ts < ts + pt.HOLD)] if f is not None and len(f) else pd.DataFrame(
        columns=["ts", "open", "high", "low", "close"])
    how, gross = outcome(px, rest, later, float(r["stop"]), float(r["target"]))
    if not _put(db, "long_trades", r["id"], "text_how", text_px=px, text_px_at=iso(ts.to_pydatetime()), text_how=how,
                text_gross=gross):
        return None
    booked = r["entry"] if r["entry"] is not None else r["est_entry"]
    return f"{r['symbol']} text ${px:.2f} vs booked ${booked:.2f} ({px / booked - 1:+.1%}; {how})"


def _armed(db: OpsStore, r: dict, prints: Prints) -> str | None:
    hit, ok = _fetch(prints, r["symbol"], pd.Timestamp(r["texted_at"]).to_pydatetime())
    if not ok:
        return None
    if hit is None:
        _put(db, "long_armed", r["id"], "px_note", px_note=NO_PRINT)
        return None
    return f"{r['symbol']} armed text ${hit[1]:.2f}" if _put(db, "long_armed", r["id"], "px_note", px=hit[1],
                                                            px_at=iso(hit[0].to_pydatetime()), px_note="ok") else None


def _put(db: OpsStore, table: str, rid: int, unset: str, **cols) -> bool:
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:                                 # fixed table and column names; written once (``unset`` still NULL)
        return c.execute(f"UPDATE {table} SET {keys} WHERE id=? AND {unset} IS NULL",  # noqa: S608
                         (*cols.values(), rid)).rowcount == 1


def line(db: OpsStore, session: str) -> str | None:
    """Digest line: the official price when each long text of the day went out, against the price the book booked."""
    with db.tx() as c:
        rows = c.execute(
            "SELECT t.symbol, t.entry, t.est_entry, t.text_px, t.text_how, t.text_gross, (SELECT a.px FROM long_armed a "
            "WHERE a.fade_id=t.fade_id AND a.px IS NOT NULL ORDER BY a.id LIMIT 1) AS armed_px FROM long_trades t WHERE "
            "t.session=? AND t.text_px IS NOT NULL ORDER BY t.id", (session,)).fetchall()
    if not rows:
        return None
    bits = []
    for r in rows:
        booked = r["entry"] if r["entry"] is not None else r["est_entry"]
        then = r["text_how"] if r["text_gross"] is None else f"from there {r['text_how']} {r['text_gross']:+.1%}"
        armed = "" if r["armed_px"] is None else f", armed text ${r['armed_px']:.2f}"
        bits.append(f"{r['symbol']} ${r['text_px']:.2f} vs booked ${booked:.2f} ({r['text_px'] / booked - 1:+.1%}; {then}{armed})")
    return "price when each long text went out (official): " + ", ".join(bits)
