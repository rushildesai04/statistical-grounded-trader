"""Execution check (system audit 2026-11-21, docs/findings/system_audit_2026-11-21.md): what the official record
assumes against what the bot could have done. Two measurements, nightly, from OFFICIAL bars.

1. Shorts. The official result sells at the open of the first minute at or after alert + 10 s (the tested timing).
   The channel's alerts reach the listener 12 to 40 s after they are posted (median by session), so on 9 of the first
   29 shorts the alert arrived AFTER that minute had opened: the official price could not be had. For every settled
   short this stores the same rule (20% stop, 60 minutes, costs, locate) from the first official regular-hours minute
   starting at or after the bot's own decision (``est_entry_at``): ``dec_entry`` / ``dec_net``, or ``dec_note`` when the
   tape has no such minute. A real market order sent at the decision fills inside the decision's minute, so the truth
   lies between the official figure and this one; both are shown.
2. Longs. An official stop exit is booked at the stop price (the minute's open when it opened below it). A stop
   order in a thin stock that is falling fills lower. ``long_trades.stop_low`` is the low of the official minute of
   each stop exit: the worst price of that minute, so the official figure and the figure at that low bracket the fill.

A MEASUREMENT: never part of a book value, a total or a trade text; one line in the nightly summary. Written once per
trade. ``bars`` is external data: a missing or malformed day is retried for LOOKBACK_DAYS and then left empty.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from sgt.fast import would_have
from sgt.fast.fade import ET
from sgt.ops.store import OpsStore, utcnow

log = logging.getLogger(__name__)
RthBars = Callable[[str, str], "pd.DataFrame | None"]   # (symbol, session) -> official regular-hours 1-minute bars
Cost = Callable[[str, datetime, float], float]
NO_MINUTE = "no official minute after the bot's decision"
LOOKBACK_DAYS, MAX_ROWS = 14, 200
REREAD = 50                                             # settled shorts with both figures at which the pair is reread


def _utc(x) -> pd.Timestamp | None:
    t = pd.to_datetime(x, utc=True, errors="coerce")
    return None if pd.isna(t) else t


def _day(bars: RthBars, symbol: str, session: str) -> pd.DataFrame | None:
    f = bars(symbol, session)
    if f is None or not len(f) or not {"ts", "open", "high", "low", "close"} <= set(f.columns):
        return None
    f = f.assign(ts=pd.to_datetime(f.ts, utc=True, errors="coerce")).dropna(subset=["ts"])
    return f.sort_values("ts").reset_index(drop=True) if len(f) else None


def run(db: OpsStore, bars: RthBars, cost_fn: Cost, now: datetime | None = None) -> list[str]:
    """Fill the two measurements for settled trades that do not have them yet. One short line per trade written."""
    now = now or utcnow()
    since = (now.astimezone(ET).date() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    with db.tx() as c:
        shorts = [dict(r) for r in c.execute(
            "SELECT id, symbol, session, est_entry_at FROM fade_trades WHERE status='settled' AND dec_net IS NULL AND "
            "dec_note IS NULL AND est_entry_at IS NOT NULL AND session>=? ORDER BY id LIMIT ?", (since, MAX_ROWS))]
        longs = [dict(r) for r in c.execute(
            "SELECT id, symbol, session, exit_ts FROM long_trades WHERE status='settled' AND exit_reason='stop' AND "
            "stop_low IS NULL AND exit_ts IS NOT NULL AND session>=? ORDER BY id LIMIT ?", (since, MAX_ROWS))]
    out = []
    for r in shorts:
        try:
            line = _short(db, r, bars, cost_fn)
        except Exception:  # noqa: BLE001 - a measurement: one trade failing never blocks the others; retried
            log.exception("execution check failed for short %s", r["id"])
            continue
        if line:
            out.append(line)
    for r in longs:
        try:
            line = _long(db, r, bars)
        except Exception:  # noqa: BLE001 - as above
            log.exception("execution check failed for long %s", r["id"])
            continue
        if line:
            out.append(line)
    return out


def _short(db: OpsStore, r: dict, bars: RthBars, cost_fn: Cost) -> str | None:
    at, day = _utc(r["est_entry_at"]), _day(bars, r["symbol"], r["session"])
    if at is None or day is None:
        return None                                        # no decision time or no bars yet: retried
    o = would_have.short_from(day, at, r["symbol"], cost_fn)
    cols = {"dec_note": NO_MINUTE} if o is None or not math.isfinite(o["net"]) else \
        {"dec_entry": float(o["entry"]), "dec_net": float(o["net"])}
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:                                     # fixed column names; written once
        done = c.execute(f"UPDATE fade_trades SET {keys} WHERE id=? AND dec_net IS NULL AND dec_note IS NULL",  # noqa: S608
                         (*cols.values(), r["id"])).rowcount == 1
    if not done:
        return None
    return f"{r['symbol']} short at the decision {cols['dec_net']:+.1%}" if "dec_net" in cols else f"{r['symbol']} short: no minute"


def _long(db: OpsStore, r: dict, bars: RthBars) -> str | None:
    at, day = _utc(r["exit_ts"]), _day(bars, r["symbol"], r["session"])
    if at is None or day is None:
        return None
    b = day[day.ts == at.floor("min")]
    if b.empty:
        return None
    low = float(b.low.iloc[0])
    if not math.isfinite(low) or low <= 0:
        return None
    with db.tx() as c:
        done = c.execute("UPDATE long_trades SET stop_low=? WHERE id=? AND stop_low IS NULL", (low, r["id"])).rowcount == 1
    return f"{r['symbol']} long stop minute low {low:.4g}" if done else None


def summary(db: OpsStore) -> dict:
    """The two measurements over everything recorded so far (the summary line and the site read this)."""
    with db.tx() as c:
        s = [dict(r) for r in c.execute(
            "SELECT net, dec_net, created_at, entry_ts FROM fade_trades WHERE status='settled' AND net IS NOT NULL AND "
            "dec_net IS NOT NULL")]
        lg = [dict(r) for r in c.execute(
            "SELECT entry, stop, exit, stop_low, net, cost_rt FROM long_trades WHERE status='settled' AND "
            "exit_reason='stop' AND stop_low IS NOT NULL AND entry > 0 AND exit > 0")]
    late = 0
    for r in s:
        got, bar = _utc(r["created_at"]), _utc(r["entry_ts"])
        late += int(got is not None and bar is not None and got > bar)
    n, m = len(s), len(lg)
    low_net = [min(r["stop_low"], r["exit"]) / r["entry"] - 1 - (r["cost_rt"] or 0.0) for r in lg]
    return {"shorts": n, "official": sum(r["net"] for r in s) / n if n else None,
            "decision": sum(r["dec_net"] for r in s) / n if n else None, "late": late,
            "long_stops": m, "stop_net": sum(r["net"] for r in lg) / m if m else None,
            "low_net": sum(low_net) / m if m else None,
            "below": sum(min(r["stop_low"], r["exit"]) / r["exit"] - 1 for r in lg) / m if m else None}


def line(db: OpsStore) -> str | None:
    """One line for the nightly summary; None while nothing is recorded."""
    s = summary(db)
    parts = []
    if s["shorts"]:
        parts.append(f"{s['shorts']} shorts official {s['official']:+.1%} a trade, {s['decision']:+.1%} from the minute "
                     f"after the bot decided; the alert reached us after the official entry minute on {s['late']}"
                     + (f" (reread at {REREAD})" if s["shorts"] < REREAD else " (REREAD DUE)"))
    if s["long_stops"]:
        parts.append(f"{s['long_stops']} long stop exits booked at the stop {s['stop_net']:+.1%} a trade, {s['low_net']:+.1%} "
                     f"at that minute's low (the low was {abs(s['below']):.1%} under the booked exit)")
    return "execution check (a measurement): " + "; ".join(parts) if parts else None
