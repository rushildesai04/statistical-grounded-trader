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

import json
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
# 2026-11-25: for an alert the live book recorded instead of shorting because its registered entry minute had passed
# (``fade.SUBSTITUTE_REASON``), what the official bars show afterwards. The live book could not know this in time.
OFF_SUBSTITUTE = "official tape traded in the registered entry minute: a later entry would have been a substitute"
OFF_DELAYED = "official tape first traded {at} ET, after the registered minute: the registered rule enters there; the live book could not confirm it in time"
OFF_NONE = "no official bar within 5 minutes of the alert: not an event under the registered rule"
ENTRY_REACTION, ENTRY_LIMIT = timedelta(seconds=10), timedelta(minutes=5)
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
    from sgt.fast import fade

    with db.read() as c:
        bases = [dict(r) for r in c.execute(
            "SELECT t.id, t.symbol, t.session, t.entry_ts, t.setup_info, f.posted_at FROM long_trades t JOIN fade_trades f ON "
            "f.id=t.fade_id WHERE t.kind='L8' AND t.entry_ts IS NOT NULL AND t.setup_official IS NULL AND t.status IN "
            "('est_closed','settled','void') AND t.session>=? ORDER BY t.id LIMIT ?", (since, MAX_ROWS))]
    for r in bases:
        try:
            line = _base(db, r, bars)
        except Exception:  # noqa: BLE001 - as above
            log.exception("setup evidence failed for long %s", r["id"])
            continue
        if line:
            out.append(line)
    with db.read() as c:
        subs = [dict(r) for r in c.execute(
            "SELECT id, symbol, session, posted_at FROM fade_trades WHERE status='skipped' AND reason=? AND "
            "entry_official IS NULL AND session>=? ORDER BY id LIMIT ?", (fade.SUBSTITUTE_REASON, since, MAX_ROWS))]
    for r in subs:
        try:
            line = _substitute(db, r, bars)
        except Exception:  # noqa: BLE001 - as above
            log.exception("entry evidence failed for alert %s", r["id"])
            continue
        if line:
            out.append(line)
    return out


# Owner 2026-11-25: "Keep feed-only, tape-confirmed, timing-different, and unknown classifications distinct." Every
# stamp starts with exactly one of these; a row with no stamp yet (no official bars read) is unknown as well.
CONFIRMED, TIMING, FEED_ONLY, UNKNOWN = "tape-confirmed: ", "timing-different: ", "feed-only: ", "unknown: "
SETUP_CLASSES = (CONFIRMED, TIMING, FEED_ONLY, UNKNOWN)


def base_on(day: pd.DataFrame, posted: pd.Timestamp, ready: pd.Timestamp) -> str:
    """What official bars show for a base break the live book took at ``ready`` (the minute after its break candle).
    The live rule reads the free feed, which carries about 1% of the volume: its ranges are never wider than the
    tape's, so a base can qualify there and not on the tape (prereg_long_setup_tape_2026-11-22: the two agreed on 4 of
    22). This is the same detector on the official candles that had closed by ``ready``. Evidence, not a rule."""
    from sgt.fast import long_shadow
    from sgt.fast import patterns as pt

    hm = lambda t: pd.Timestamp(t).tz_convert(ET).strftime("%H:%M")  # noqa: E731
    seen = day[day.ts + pt.BAR <= ready].reset_index(drop=True)
    _, t0 = long_shadow.setups(seen, {"posted_at": posted.isoformat(), "status": "live"})
    if t0 is None:
        return UNKNOWN + "official tape: no start bar for this alert within its limits"
    found = pt.base_break(seen, t0)
    brk = ready - pt.BAR
    if found and found[0].ready == ready:
        i = found[0].info
        return (f"{CONFIRMED}official tape: the same base break at {hm(brk)} (base {i['base_low']:g} to {i['base_high']:g}, "
                f"{(i['base_high'] - i['base_low']) / i['base_low']:.2%} wide)")
    if found:
        return f"{TIMING}official tape: its first base break was at {hm(found[0].info['break_ts'])}, not {hm(brk)}"
    j = seen.index[seen.ts == brk]
    if not len(j):
        return f"{FEED_ONLY}official tape: no base break by {hm(brk)} and no official candle in that minute"
    j = int(j[0])
    a = int(seen.ts.searchsorted(brk - pt.BASE_MIN, side="left"))
    base = seen.iloc[a:j]
    if len(base) < pt.BASE_MIN_BARS:
        return f"{FEED_ONLY}official tape: no base break at {hm(brk)}: only {len(base)} candles in the 10 minutes before it"
    hi, lo, close = float(base.high.max()), float(base.low.min()), float(seen.close.iloc[j])
    vw = float(pt.session_vwap(seen)[j])
    why = []
    if lo > 0 and (hi - lo) / lo > pt.BASE_MAX_RANGE:
        why.append(f"the 10 minutes before it span {(hi - lo) / lo:.2%} ({lo:g} to {hi:g}; limit {pt.BASE_MAX_RANGE:.0%})")
    if not close > hi:
        why.append(f"its close {close:g} is not above their high {hi:g}")
    if not close > vw:
        why.append(f"its close {close:g} is not above session VWAP {vw:.4g}")
    if not brk <= t0 + pt.BASE_WATCH or not brk >= t0 + pt.BASE_MIN:
        why.append("it is outside the watch window on the tape's start bar")
    if not why:                                            # the detector said no and the parts do not show why
        return f"{UNKNOWN}official tape: no base break at {hm(brk)}, reason not reproduced"
    return f"{FEED_ONLY}official tape: no base break at {hm(brk)}: " + "; ".join(why)


def _base(db: OpsStore, r: dict, bars: RthBars) -> str | None:
    posted, ready, day = _utc(r["posted_at"]), _utc(r["entry_ts"]), _day(bars, r["symbol"], r["session"])
    # The setup's own break candle when the row has it (rows from 2026-11-25): on a thin feed a buy at the NEXT candle
    # can be booked a minute or more after the break, and the entry time is then not the minute after the break.
    try:
        seen = json.loads(r.get("setup_info") or "{}")
        brk = _utc(seen.get("break_ts")) if isinstance(seen, dict) else None
    except (TypeError, ValueError):
        brk = None
    if brk is not None:
        ready = brk + pd.Timedelta(minutes=1)
    if posted is None or ready is None or day is None or "volume" not in day.columns:
        return None                                        # no bars yet: retried
    what = base_on(day, posted, ready)[:400]
    with db.tx() as c:
        done = c.execute("UPDATE long_trades SET setup_official=? WHERE id=? AND setup_official IS NULL",
                         (what, r["id"])).rowcount == 1
    return f"{r['symbol']} base break: {what}" if done else None


def _substitute(db: OpsStore, r: dict, bars: RthBars) -> str | None:
    """What the official bars show for an alert recorded under the 2026-11-25 entry rule: did the stock trade in its
    registered entry minute (the live entry would have been a substitute), first trade later (the registered rule
    enters there: an entry the live book gave up because it could not confirm the delay), or not within 5 minutes."""
    from sgt.fast import fade

    posted, day = _utc(r["posted_at"]), _day(bars, r["symbol"], r["session"])
    if posted is None or day is None:
        return None                                        # no bars yet: retried
    start = pd.Timestamp(fade.entry_start(posted.to_pydatetime()))
    after = day[day.ts >= posted + ENTRY_REACTION]
    if after.empty or after.ts.iloc[0] - posted > ENTRY_LIMIT:
        what = OFF_NONE
    elif after.ts.iloc[0] < start + pd.Timedelta(minutes=1):
        what = OFF_SUBSTITUTE
    else:
        what = OFF_DELAYED.format(at=after.ts.iloc[0].tz_convert(ET).strftime("%H:%M"))
    with db.tx() as c:
        done = c.execute("UPDATE fade_trades SET entry_official=? WHERE id=? AND entry_official IS NULL",
                         (what, r["id"])).rowcount == 1
    short = {OFF_SUBSTITUTE: "the official tape traded in its registered minute", OFF_NONE: "no official bar within 5 minutes"}
    return (f"{r['symbol']} recorded, not shorted: "
            + short.get(what, "the official tape first traded in a later minute")) if done else None


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
