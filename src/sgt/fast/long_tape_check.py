"""Official-tape stop check for the live LONG book (owner 2026-11-21, decision 2 C after the execution baseline).

The long book watches the free feed: one exchange, under 1% of the volume in these stocks. Of 14 long stops that the
official tape crossed in the first three weeks, the feed showed 9 within a minute, 2 came 14 and 21 minutes late and
3 never came (ADXN and XLAB on 2026-11-12 were texted as "target hit" and are official stop losses). The official
one-minute bars are free once about 15 minutes old. Every pass this reads them for each live long and applies the
trade's OWN stop to them:
- a long still open whose stop the official tape crossed is closed there (reason ``LATE``) with its LONG OUT text;
- a long the book closed on its target, its timer or its red candle AFTER the official tape had crossed its stop is
  corrected to the stop and ONE correction is texted.
A stop counts only if the tape had not reached the target in an earlier minute; in one minute the stop comes first
(the rule of the official replay, ``patterns.simulate``).

This shortens the time a wrong text stands. It is NOT stop protection: the correction arrives a quarter of an hour
after the price (docs/findings/exec_baseline_2026-11-21.md, section 3). Nothing official changes: the nightly settle
and the execution ledger still compute their own results from the tape. ``bars`` is external data: anything that is not
a sane bar is ignored, and a failure for one trade never stops the others.
"""
from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from sgt.fast import est_costs, fade
from sgt.fast import patterns as pt
from sgt.fast.fade import ET
from sgt.fast.tape_check import DELAY, LATE, RECHECK, Bars
from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
CORRECTABLE = ("target", "time", "red")           # exits the live feed chose; a live "stop" is already the stop
MAX_ROWS = 60


def first_stop(bars: pd.DataFrame | None, entry_ts: datetime, before: datetime, stop: float,
               target: float | None) -> tuple[datetime, float] | None:
    """The first official minute from the entry's minute, starting before ``before``, whose low reaches ``stop``:
    (minute start, sale price = the stop, or the minute's open when it opened under it). None when no minute does, or
    when an EARLIER minute had already reached ``target``. Malformed rows are dropped."""
    if bars is None or not len(bars) or not {"ts", "open", "high", "low"} <= set(bars.columns):
        return None
    f = bars[["ts", "open", "high", "low"]].copy()
    f["ts"] = pd.to_datetime(f.ts, utc=True, errors="coerce")
    for c in ("open", "high", "low"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    f = f.dropna()
    f = f[(f.ts >= pd.Timestamp(entry_ts).floor("min")) & (f.ts < pd.Timestamp(before)) & (f.low > 0)
          & (f.high >= f.low)].sort_values("ts")
    for b in f.itertuples(index=False):
        if b.low <= stop:
            return b.ts.to_pydatetime(), float(min(stop, b.open))
        if target is not None and b.high >= target:
            return None
    return None


def _rows(db: OpsStore, now: datetime) -> list[dict]:
    marks = ",".join("?" * len(CORRECTABLE))
    with db.tx() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM long_trades WHERE est_entry > 0 AND entry_ts IS NOT NULL AND stop > 0 AND (status='open' OR "
            f"(status='est_closed' AND reason IN ({marks}) AND est_exit_at>=?)) ORDER BY id LIMIT ?",  # noqa: S608
            (*CORRECTABLE, iso(now - RECHECK - DELAY), MAX_ROWS))]


def check(db: OpsStore, send, bars: Bars, now: datetime | None = None) -> int:
    """One pass over the live longs. Returns how many were closed or corrected."""
    from sgt.fast import long_book

    now, n = now or utcnow(), 0
    for r in _rows(db, now):
        try:
            entry, started = float(r["est_entry"]), datetime.fromisoformat(r["entry_ts"])
            was_open = r["status"] == "open"
            until = now - DELAY if was_open else min(datetime.fromisoformat(r["est_exit_at"]), now - DELAY)
            if until <= started:
                continue
            hit = first_stop(bars(r["symbol"], pd.Timestamp(started).floor("min").to_pydatetime(), until), started, until,
                             float(r["stop"]), r.get("target"))
            if hit is None:
                continue
            at, px = hit
            with db.tx() as c:
                done = c.execute("UPDATE long_trades SET status='est_closed', est_exit=?, est_exit_at=?, est_last=?, "
                                 "reason=? WHERE id=? AND status=? AND reason IS NOT ?",
                                 (px, iso(at), px, LATE, r["id"], r["status"], LATE)).rowcount == 1
            if not done:
                continue
            n += 1
            if was_open:
                t = pt.Trade(r["kind"], "long", pd.Timestamp(started), entry, pd.Timestamp(at), px, "stop", px / entry - 1)
                send(long_book.exit_text(db, r, t) + f"\nSeen late: the stop traded at {at.astimezone(ET):%H:%M} ET on "
                                                     "the official tape; the free live feed did not show it.")
            else:
                send(correction_text(db, r, entry, px, at))
        except Exception:  # noqa: BLE001 - one trade failing never stops the others; logged with its id
            log.exception("official-tape check failed for long %s", r.get("id"))
    return n


def correction_text(db: OpsStore, r: dict, entry: float, px: float, at: datetime) -> str:
    """The one text that replaces a long's result already texted from the free feed."""
    from sgt.fast import long_book

    cost = est_costs.cost("long", r["symbol"], r.get("entry_ts"), entry) or 0.0
    was_px = float(r["est_exit"])
    g, was = px / entry - 1 - cost, was_px / entry - 1 - cost
    usd = (r["weight"] or fade.WEIGHT) * long_book.nav(db) * g
    sign = ("+" if usd >= 0 else "-") + f"${abs(usd):,.0f}"
    return (f"[SGT] ✏️ CORRECTION · {r['symbol']} long: the stop traded at {at.astimezone(ET):%H:%M} ET on the official "
            f"tape, before our sale. The free live feed did not show it.\n"
            f"Result: {g:+.1%} ({sign}), not the {was:+.1%} texted earlier. Bought ${entry:.4g}, stop ${px:.4g} "
            f"(we sold ${was_px:.4g} later: {r['reason']}).\n"
            f"Estimate after about {cost:.1%} costs; the official number comes tonight. A late correction, not stop "
            "protection.")
