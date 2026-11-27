"""Official-tape stop check for the live fade (owner 2026-11-17, decision 3A).

The live book watches the free feed. On a thin name that feed can miss a whole move: MPU 2026-11-17 traded $2.05 on
the official tape at 10:54, above the $1.848 stop, while the free feed printed 12 one-minute bars in the hour (highest
$1.80). The book held to its 60-minute timer and texted -12.1%; the official result was the stop, about -21%.

The official one-minute bars are free once about 15 minutes old. Every minute this check reads them for each live
short and applies the trade's OWN stop level to them:
- a trade still open whose stop the official tape crossed is closed there (reason ``LATE``) and texted;
- a trade the book closed on its timer (or on the owner's NO) AFTER the official tape had crossed its stop is
  corrected to the stop and a correction is texted, once.
So the texted estimate and the site agree with the official result within about a quarter of an hour instead of at
the nightly settle. Nothing official changes: the settle still computes its own result from the same tape.

The entry minute's own bar is not read (inside one minute the order of the entry and the high is unknown; the live
monitor covers that minute). ``bars`` is external data: anything that is not a sane bar is ignored, and a failure for
one trade never stops the others.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Callable

import pandas as pd

from sgt.fast import est_costs
from sgt.fast.fade import WEIGHT, day_est_pnl, exit_source, nav
from sgt.fast.fade_rule import ET, STOP, VETO_EXIT
from sgt.ops.store import OpsStore, iso, utcnow

log = logging.getLogger(__name__)
DELAY = timedelta(minutes=16)            # an official minute bar is final and free this long after it started
RECHECK = timedelta(minutes=25)          # a closed trade's last minutes arrive this late at most: checked until then
LATE = "stop (official tape, seen late)"
SRC = "official tape, one-minute candle, read once it is free (about 16 minutes old)"
AT_LEVEL = "candle start, not a print time: the cover is the stop level (the candle's high reached it)"
AT_OPEN = "candle start, not a print time: the cover is the candle's open (it opened above the stop)"
CORRECTABLE = ("60 min", "close", VETO_EXIT)        # exits the live feed chose; a live "stop" is already the stop
MAX_ROWS = 60
Bars = Callable[[str, datetime, datetime], pd.DataFrame]   # (symbol, start, end) -> official 1-minute bars, ts = start


def first_cross(bars: pd.DataFrame | None, after: datetime, before: datetime, level: float) -> tuple[datetime, float] | None:
    """The first official bar starting in (the entry's minute, ``before``) whose high reaches ``level``: (bar start,
    cover price). The cover is the stop level, or the bar's open when it opened above it (a gap). Malformed rows are
    dropped."""
    if bars is None or not len(bars) or not {"ts", "open", "high"} <= set(bars.columns):
        return None
    f = bars[["ts", "open", "high"]].copy()
    f["ts"] = pd.to_datetime(f.ts, utc=True, errors="coerce")
    for c in ("open", "high"):
        f[c] = pd.to_numeric(f[c], errors="coerce")
    f = f.dropna()
    start = pd.Timestamp(after).floor("min") + pd.Timedelta(minutes=1)        # the minute after the entry's own
    f = f[(f.ts >= start) & (f.ts < pd.Timestamp(before)) & (f.open > 0) & (f.high >= f.open)].sort_values("ts")
    hit = f[f.high >= level]
    if hit.empty:
        return None
    b = hit.iloc[0]
    return b.ts.to_pydatetime(), float(max(level, b.open))


def _rows(db: OpsStore, now: datetime) -> list[dict]:
    marks = ",".join("?" * len(CORRECTABLE))
    # A read (2026-11-24): under tx() this select held the write lock 0.37 s on 11-24, with the long book's exit check
    # and the context pass waiting behind it.
    with db.read() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM fade_trades WHERE est_entry IS NOT NULL AND est_entry_at IS NOT NULL AND (status='open' OR "
            f"(status='est_closed' AND reason IN ({marks}) AND est_exit_at>=?)) ORDER BY id LIMIT ?",
            (*CORRECTABLE, iso(now - RECHECK - DELAY), MAX_ROWS))]


def check(db: OpsStore, send, bars: Bars, now: datetime | None = None) -> int:
    """One pass over the live shorts. Returns how many were closed or corrected."""
    from sgt.fast.fade_texts import out_text

    now, n = now or utcnow(), 0
    for r in _rows(db, now):
        try:
            entry, started = float(r["est_entry"]), datetime.fromisoformat(r["est_entry_at"])
            was_open = r["status"] == "open"
            until = now - DELAY if was_open else min(datetime.fromisoformat(r["est_exit_at"]), now - DELAY)
            if until <= started:
                continue
            hit = first_cross(bars(r["symbol"], started, until), started, until, entry * (1 + STOP))
            if hit is None:
                continue
            at, px = hit
            # 2026-11-27: this rewrite replaces the row's exit estimate, so it replaces the record of where that
            # estimate came from too (the live look's own source stays in the decision log, on the row's first
            # "est_closed" entry). ``est_exit_at`` stays the candle's start, as before; the time this check decided
            # is ``exit_decided_at``. The cover is a rule price off a candle, not a print: the note says which.
            src = exit_source(now, SRC, px, at)
            src["exit_src_note"] = AT_OPEN if px > entry * (1 + STOP) + 1e-9 else AT_LEVEL
            with db.tx() as c:
                done = c.execute("UPDATE fade_trades SET status='est_closed', est_exit=?, est_exit_at=?, est_last=?, "
                                 "est_last_print_at=NULL, reason=?, exit_decided_at=?, exit_src=?, exit_src_px=?, "
                                 "exit_src_at=?, exit_src_age_s=?, exit_src_note=? "
                                 "WHERE id=? AND status=? AND reason IS NOT ?",
                                 (px, iso(at), px, LATE, src["exit_decided_at"], src["exit_src"], src["exit_src_px"],
                                  src["exit_src_at"], src["exit_src_age_s"], src["exit_src_note"],
                                  r["id"], r["status"], LATE)).rowcount == 1
                if done and was_open:        # a NO after the exit is answered "already closed", never acted on
                    c.execute("UPDATE approvals SET status='expired', decided_at=?, result='trade closed' WHERE "
                              "strategy=? AND kind='fadeexit' AND status='pending'", (iso(now), f"fade:{r['id']}"))
            if not done:
                continue
            n += 1
            db.decide("short", r["id"], r["symbol"], r["status"], "est_closed", LATE, None, exit=px, exit_at=iso(at),
                      official_tape_read_at=iso(now), was=r.get("reason"))
            cost = est_costs.cost("short", r["symbol"], r["est_entry_at"], entry) or 0.0
            g = (entry - px) / entry - cost
            v = nav(db)
            usd = g * (r["weight"] or WEIGHT) * v
            if was_open:
                with db.tx() as c:
                    n_day = c.execute("SELECT COUNT(*) FROM fade_trades WHERE session=? AND status='est_closed'",
                                      (r["session"],)).fetchone()[0]
                send(out_text(r, entry, px, LATE, usd, day_est_pnl(db, r["session"]) * v, n_day, cost=cost))
            else:
                send(correction_text(r, entry, px, at, g, usd, cost))
        except Exception:  # noqa: BLE001 - one trade failing never stops the others; logged with its id
            log.exception("official-tape check failed for fade %s", r.get("id"))
    return n


def correction_text(r: dict, entry: float, px: float, at: datetime, g: float, usd: float, cost: float) -> str:
    """The one text that replaces a result already texted from the free feed."""
    was_px = float(r["est_exit"])
    was = (entry - was_px) / entry - cost
    sign = lambda x: ("+" if x >= 0 else "-") + f"${abs(x):,.0f}"  # noqa: E731
    return (f"[SGT] ✏️ CORRECTION · {r['symbol']} short: the stop was hit at {at.astimezone(ET):%H:%M} ET on the "
            f"official tape, before our cover. The free live feed did not show it.\n"
            f"Result: {g:+.1%} ({sign(usd)}), not the {was:+.1%} texted earlier. Shorted ${entry:.4g}, stop "
            f"${px:.4g} (we covered ${was_px:.4g} later on the timer).\n"
            f"Estimate after about {cost:.1%} costs; the official number comes tonight.")
