"""Where in its move a stock was when the alert came (owner 2026-11-18: "account for many different scenarios").

From `prereg_scenario_map_2026-11-18`: RUN = the price at the alert's start bar over the lowest low of the day so
far, minus 1; OFF = that price over the highest high of the day so far, minus 1; both from OFFICIAL bars before the
start bar, pre-market included. Bands by the training terciles of RUN (fixed there, never re-estimated): early up to
33.6%, middle up to 63.1%, late above; "at the high" when OFF is no lower than -2%. The median alert of the channel
arrives 45% above the day's low.

A LABEL, not a rule: every alert is tagged nightly, once its official bars are complete, so that scenario rules can
be judged on new alerts (`prereg_early_alert_fade_forward_2026-11-18`). Nothing here is read by a book.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from sgt.fast import fade
from sgt.fast.fade import ET
from sgt.ops.store import OpsStore, utcnow

log = logging.getLogger(__name__)
RUN_EDGES = (0.3363915654238234, 0.6308226537216828)     # reports/scenario_map/result_train.json, 2026-11-18
AT_HIGH = -0.02
MIN_BEFORE = 5
BANDS = ("early", "middle", "late")
LOOKBACK_DAYS, MAX_ROWS = 14, 400
SIP_DELAY = timedelta(minutes=20)                        # official bars are complete this long after the close
NO_START, FEW_BARS = "no start bar in regular hours", "fewer than 5 bars before the alert"
DayBars = Callable[[str, str], pd.DataFrame]             # (symbol, session) -> that day's official bars, all segments


def band(run: float) -> str:
    return BANDS[0] if run <= RUN_EDGES[0] else BANDS[1] if run <= RUN_EDGES[1] else BANDS[2]


def where(off: float) -> str:
    return "at the high" if off >= AT_HIGH else "off the high"


def state(day: pd.DataFrame, t0: pd.Timestamp) -> dict | None:
    """RUN and OFF at ``t0`` from bars BEFORE it (all segments of the day). None = too little to say."""
    before, at = day[day.ts < t0], day[day.ts == t0]
    if len(before) < MIN_BEFORE or at.empty:
        return None
    px, lo, hi = float(at.open.iloc[0]), float(before.low.min()), float(before.high.max())
    if not (px > 0 and lo > 0 and hi > 0):
        return None
    return {"run": px / lo - 1, "off": px / hi - 1}


def start_bar(day: pd.DataFrame, posted: pd.Timestamp) -> pd.Timestamp | None:
    """The research start bar: the first regular-hours bar at or after alert + 10 s, within 5 minutes of the alert."""
    rth = day[day.segment == "rth"]
    nxt = rth[rth.ts >= posted + pd.Timedelta(fade.REACTION)]
    if nxt.empty or nxt.ts.iloc[0] - posted > pd.Timedelta(fade.MAX_ENTRY_DELAY):
        return None
    return nxt.ts.iloc[0]


def _close(session: str) -> datetime:
    from datetime import date

    from sgt.intraday.calendar import default_calendar
    return default_calendar().bounds(date.fromisoformat(session)).rth_close


def tag(db: OpsStore, bars: DayBars, now: datetime | None = None) -> list[str]:
    """Label every alert not labelled yet whose official bars are complete. One line per alert labelled."""
    now = now or utcnow()
    since = (now.astimezone(ET).date() - timedelta(days=LOOKBACK_DAYS)).isoformat()
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT id, symbol, session, posted_at FROM fade_trades WHERE scen_band IS NULL AND scen_note IS NULL AND "
            "session>=? ORDER BY id LIMIT ?", (since, MAX_ROWS))]
    out = []
    for r in rows:
        try:
            if pd.Timestamp(now) < pd.Timestamp(_close(r["session"])) + SIP_DELAY:
                continue
            day = bars(r["symbol"], r["session"])
            if day is None or not len(day):
                continue                                  # bars not there yet: asked again another night
            day = day.assign(ts=pd.to_datetime(day.ts, utc=True)).sort_values("ts").reset_index(drop=True)
            t0 = start_bar(day, pd.Timestamp(r["posted_at"]).tz_convert("UTC"))
            st = state(day, t0) if t0 is not None else None
            cols = ({"scen_note": NO_START if t0 is None else FEW_BARS} if st is None else
                    {"scen_run": st["run"], "scen_off": st["off"], "scen_band": band(st["run"]), "scen_where": where(st["off"])})
            keys = ", ".join(f"{k}=?" for k in cols)
            with db.tx() as c:                            # fixed column names; written once
                done = c.execute(f"UPDATE fade_trades SET {keys} WHERE id=? AND scen_band IS NULL AND scen_note IS NULL",  # noqa: S608
                                 (*cols.values(), r["id"])).rowcount == 1
            if done and st is not None:
                out.append(f"{r['symbol']} {band(st['run'])}, {where(st['off'])}")
        except Exception:  # noqa: BLE001 - one alert failing never blocks the others; logged, retried next run
            log.exception("scenario tag for %s %s failed", r["symbol"], r["id"])
    return out


def line(db: OpsStore, session: str) -> str | None:
    """Digest line: where each alert that became a trade (short or long) arrived in its move."""
    with db.tx() as c:
        rows = c.execute(
            "SELECT f.symbol, f.scen_run, f.scen_off, f.scen_band, f.scen_where FROM fade_trades f WHERE f.session=? AND "
            "f.scen_band IS NOT NULL AND (f.status IN ('settled','est_closed','open') OR EXISTS (SELECT 1 FROM long_trades t "
            "WHERE t.fade_id=f.id AND t.status IN ('open','est_closed','settled','seen'))) ORDER BY f.id", (session,)).fetchall()
    if not rows:
        return None
    return "where each traded alert arrived: " + ", ".join(
        f"{r['symbol']} {r['scen_band']}, {r['scen_where']} (up {r['scen_run']:.0%} from the low)" for r in rows)


def count(db: OpsStore, since: str = "2026-11-04") -> dict:
    """How many alerts carry a band (the forward test reads only this until its decision night)."""
    with db.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session>=? AND scen_band IS NOT NULL", (since,)).fetchone()[0]
        m = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session>=? AND scen_note IS NOT NULL", (since,)).fetchone()[0]
    return {"labelled": int(n), "no_label": int(m)}
