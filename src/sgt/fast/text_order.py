"""A long that was texted is never cut from the record (owner 2026-11-18: "you can't cut trades that already happened
in real life ... model this like actual real life").

The long book's official result replays the live trade's own tested order on the official tape. When that order does
not fill there (the thin live feed showed a trigger the full tape did not), the trade used to become VOID: gone from
the book and the nightly summary although its entry and its exit had been texted (GMM 2026-11-18: texted bought
$1.785, sold $1.88; the official fill minute opened at the stop). A real account would have sent an order when the
text went out and would have left the stop and the target resting at the broker, so the official result of such a
trade is what the system DID, at the prices that were really there:
- bought at the first official print at or after the fill text (within WINDOW of it; else the open of the first
  official 1-minute bar after the text);
- the trade's own stop and target on the full tape from there; else sold at the close where the live trade's hold
  ended (HOLD after its live fill bar);
- a live trade that left on its red-candle rule sold in the same moment it was texted (the fill bar had already
  closed under the entry): bought and sold at once. So is a price already at or under the stop, or at or over the
  target. The cost is the result.
No exit rule is re-decided from the new entry price: the red-candle exit is taken only when the live trade took it.
Only when the full tape has no trade at all after the text is the long void: it could not have happened.

``long_book.settle`` leaves such a trade ``est_closed`` with a reason starting with ``NO_FILL`` that keeps the live
exit; ``run`` (the same nightly job, right after it) books the result. External bytes: ``prints`` returns cleaned rows or None on a failed request (retried the
next night; after STALE_DAYS the bars alone decide); rows per run are bounded.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

from sgt.fast import calc_v1, long_book, text_px
from sgt.fast import patterns as pt
from sgt.fast.fade import ET
from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
REASON = "official: bought at the text (the tested order did not fill on the full tape)"
IMPOSSIBLE = "no trade on the full tape after the text"
MAX_ROWS = 60
NO_PRINTS = np.empty((0, 3))


def result(px: float, ts: pd.Timestamp, rest: np.ndarray, bars: pd.DataFrame, stop: float, target: float,
           live_red: bool, until: pd.Timestamp) -> dict:
    """Buying ``px`` at ``ts``: how it ended, at what price and when. ``rest`` = the later prints of that minute
    (ns, price, size); ``bars`` = the session's official 1-minute bars (UTC ``ts``); ``live_red`` = the live trade
    left on its red-candle rule; ``until`` = where the live trade's hold ended (bars starting before it count)."""
    if px <= stop:
        return {"how": "stop", "exit": px, "exit_ts": ts}                 # bought into a broken stop: sold at once
    if px >= target:
        return {"how": "target", "exit": px, "exit_ts": ts}               # already past the target: sold at once
    if live_red:
        return {"how": "red", "exit": px, "exit_ts": ts}                  # the live trade sold as it was texted
    for t, p in ((int(x[0]), float(x[1])) for x in rest):
        if p <= stop:
            return {"how": "stop", "exit": min(stop, p), "exit_ts": pd.Timestamp(t, tz="UTC").floor("us")}
        if p >= target:
            return {"how": "target", "exit": target, "exit_ts": pd.Timestamp(t, tz="UTC").floor("us")}
    minute = ts.floor("min")
    later = bars[(bars.ts > minute) & (bars.ts < until)]
    for b in later.itertuples():
        if b.low <= stop:
            return {"how": "stop", "exit": min(stop, float(b.open)), "exit_ts": b.ts}
        if b.high >= target:
            return {"how": "target", "exit": max(target, float(b.open)), "exit_ts": b.ts}
    if len(later):
        return {"how": "time", "exit": float(later.close.iloc[-1]), "exit_ts": later.ts.iloc[-1]}
    own = bars[bars.ts == minute]
    last = float(own.close.iloc[0]) if len(own) else (float(rest[-1][1]) if len(rest) else px)
    return {"how": "time", "exit": last, "exit_ts": minute}


def live_exit(r: dict) -> str:
    """How the live trade left: kept in the waiting reason; for a row from before that, read off the live record."""
    kept = str(r.get("reason") or "").partition("; live exit ")[2]
    if kept in ("stop", "target", "red", "time"):
        return kept
    x, stop, target = r.get("est_exit"), float(r["stop"]), r.get("target")
    if x is None:
        return "time"
    if x <= stop:
        return "stop"
    if target is not None and x >= float(target):
        return "target"
    try:
        soon = pd.Timestamp(r["est_exit_at"]) - pd.Timestamp(r["entry_ts"]) <= pt.BAR
    except (TypeError, ValueError):
        soon = False
    return "red" if soon else "time"


def run(db: OpsStore, prints: text_px.Prints, bars: long_book.SipBars, cost_fn: long_book.Cost,
        now: datetime | None = None) -> list[str]:
    """Book the official result of every long waiting for it. One line per trade booked or voided."""
    now = now or utcnow()
    stale = (now.astimezone(ET).date() - timedelta(days=long_book.STALE_DAYS)).isoformat()
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, symbol, session, kind, stop, target, rules, created_at, reason, entry_ts, est_exit, est_exit_at "
            "FROM long_trades WHERE status='est_closed' AND substr(reason, 1, ?)=? ORDER BY id LIMIT ?",
            (len(long_book.NO_FILL), long_book.NO_FILL, MAX_ROWS))]
    out = []
    for r in rows:
        try:
            if pd.Timestamp(now) < pd.Timestamp(long_book.default_close(r["session"])) + long_book.SIP_DELAY:
                continue                               # the session's official data is not complete yet
            line = _one(db, r, prints, bars, cost_fn, now, r["session"] <= stale)
        except Exception:  # noqa: BLE001 - one trade failing never blocks the others; logged, retried next run
            log.exception("order at the text for %s %s failed", r["symbol"], r["id"])
            continue
        if line:
            out.append(line)
    return out


def _void(db: OpsStore, r: dict, why: str) -> str | None:
    return f"{r['symbol']} {r['kind']} void ({why})" if long_book._set(db, r["id"], "est_closed", status="void", reason=why) else None


def _one(db: OpsStore, r: dict, prints: text_px.Prints, bars: long_book.SipBars, cost_fn: long_book.Cost,
         now: datetime, stale: bool) -> str | None:
    f = bars(r["symbol"], r["session"])
    if f is None or not len(f):
        return _void(db, r, "no official SIP bars") if stale else None        # an outage day is retried
    f = f.assign(ts=pd.to_datetime(f.ts, utc=True)).sort_values("ts").reset_index(drop=True)
    sent = pd.Timestamp(r["created_at"])
    sent = sent.tz_localize("UTC") if sent.tzinfo is None else sent.tz_convert("UTC")
    tr = prints(r["symbol"], sent.to_pydatetime(), (sent + text_px.WINDOW).ceil("min").to_pydatetime())
    if tr is None and not stale:
        return None                                    # the prints request failed: asked again tomorrow
    hit = text_px.first_print(tr, sent.to_pydatetime())
    if hit is not None:
        ts, px, rest = hit
    else:                                              # no print that soon (or no prints at all): the next bar's open
        nxt = f[f.ts >= sent.ceil("min")]
        if nxt.empty or not float(nxt.open.iloc[0]) > 0:
            return _void(db, r, IMPOSSIBLE)
        ts, px, rest = nxt.ts.iloc[0], float(nxt.open.iloc[0]), NO_PRINTS
    stop = float(r["stop"])
    target = float(r["target"]) if r["target"] is not None else float("inf")
    booked = pd.to_datetime(r.get("entry_ts"), utc=True, errors="coerce")
    until = (booked if pd.notna(booked) else ts.floor("min")) + pt.HOLD
    res = result(px, ts, rest, f, stop, target, live_exit(r) == "red", until)
    gross = res["exit"] / px - 1
    rt = float(cost_fn(r["symbol"], ts.to_pydatetime(), px))
    net = gross - rt
    if not long_book._set(db, r["id"], "est_closed", status="settled", entry=px, fill_ts=iso(ts.to_pydatetime()),
                          exit=res["exit"], exit_ts=iso(pd.Timestamp(res["exit_ts"]).to_pydatetime()), exit_reason=res["how"],
                          gross=gross, cost_rt=rt, net=net, settled_at=iso(now), reason=REASON,
                          # 2026-11-25: this result is not a threshold replay; the parallel figure says so explicitly
                          calc=calc_v1.PRIMARY,
                          calc_v1=calc_v1.not_computed("scored as the order sent at the text")["calc_v1"]):
        return None
    return f"{r['symbol']} {r['kind']} {net:+.2%} (bought at the text ${px:.2f}; {res['how']})"
