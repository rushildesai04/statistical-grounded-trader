"""What each alert the short book did NOT take would have made (owner 2026-11-18, option A: keep the gates in the
money book; show every blocked alert on the site with its reason and what it would have made; never in a total).

Nightly, from OFFICIAL bars, for every alert stored as filtered or skipped: the rule's own short as if no gate had
stopped it: sold at the open of the first regular-hours minute at or after alert + 10 s (within 5 minutes of the
alert), the 20% stop, 60 minutes (or the close), the same costs and locate. For an alert that is a tested event this
is the tested result; for one outside the rule (price under $1, after the last entry time) it is the same order all
the same. No result, and ``wh_note`` says why, when there was no regular-hours trade within 5 minutes of the alert
(before the open, after the close, halted).

A MEASUREMENT: never part of a book value, a total or a text. A short on a stock with no shares to borrow could not
have been placed by a real account: its number says what the gate cost or saved on paper, not what was available.
``short_from`` is also the official result of a short that WAS taken but is not a tested event (``fade_live``).
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta
from typing import Callable

import numpy as np
import pandas as pd

from sgt.fast import fade, long_book
from sgt.fast.est_costs import LOCATE
from sgt.fast.fade import ET
from sgt.fast.fade_rule import HOLD, STOP
from sgt.ops.store import OpsStore, utcnow

log = logging.getLogger(__name__)
DayBars = Callable[[str, str], "pd.DataFrame | None"]     # (symbol, session) -> official 1-minute bars with ``segment``
Cost = Callable[[str, datetime, float], float]            # round-trip cost fraction at (symbol, entry time, price)
NOT_TAKEN = ("filtered", "skipped")
NO_TRADE = "no regular-hours trade within 5 minutes of the alert"
NO_BARS = "no official bars"
LOOKBACK_DAYS, STALE_DAYS, MAX_ROWS = 14, 3, 600
# The gates that are a CHOICE (a short a real account could have taken), each with the tested average of the shorts
# it stops: a gate is right while that stays below zero. Price under $1, no borrow and halts are not choices.
# (label, tested average of the stopped shorts or None when the test gave no single figure, reason prefix)
# Tested: news fades -0.20% (training; held-out -0.60%), repeats -1.10% (450 training and held-out fades), beta >= 3
# -2.14% a trade (playbook section 2).
GATES = (("news", -0.0020, fade.NEWS_REASON[:4]), ("hours", None, "outside "), ("repeat", -0.0110, fade.REPEAT_REASON[:20]),
         ("beta", -0.0214, fade.BETA_REASON[:9]))
REREAD = 50                                  # stock-days at which a gate whose blocked shorts are winning is put to the owner
FLAG_MIN, FLAG_Z = 5, 1.645                  # a gate is marked only on 5+ stock-days whose 95% lower bound is above zero
AUDIT_MAX_ROWS = 20_000


def cover(w: pd.DataFrame, entry: float, stop: float | None = STOP) -> float | None:
    """Cover price of a short at ``entry`` over the bars ``w``: the first bar whose high reaches entry x (1 + stop)
    covers at max(that level, its open); else the last close. The research rule (scripts/alert_fade_test.cover)."""
    if w.empty:
        return None
    if stop is not None:
        lvl = entry * (1 + stop)
        hit = np.flatnonzero(w.high.to_numpy(float) >= lvl)
        if len(hit):
            return float(max(lvl, float(w.open.iloc[hit[0]])))
    return float(w.close.iloc[-1])


def short_from(rth: pd.DataFrame, at: pd.Timestamp, symbol: str, cost_fn: Cost, not_after: pd.Timestamp | None = None) -> dict | None:
    """The rule's short from the first regular-hours minute starting at or after ``at`` (no later than ``not_after``):
    entry at its open, the stop, HOLD, costs and locate. ``rth``: regular-hours bars, UTC ``ts``, time-ordered.
    None = no such minute."""
    nxt = rth[rth.ts >= at.ceil("min")]
    if nxt.empty or (not_after is not None and nxt.ts.iloc[0] > not_after) or not float(nxt.open.iloc[0]) > 0:
        return None
    entry_ts, entry = nxt.ts.iloc[0], float(nxt.open.iloc[0])
    c = cover(nxt[nxt.ts < entry_ts + HOLD], entry)
    if c is None:
        return None
    rt = float(cost_fn(symbol, entry_ts.to_pydatetime(), entry))
    g = (entry - c) / entry
    return {"entry_ts": entry_ts, "entry": entry, "cost_rt": rt, "gross": g, "net": g - rt - LOCATE, "cover": c}


def run(db: OpsStore, bars: DayBars, cost_fn: Cost, now: datetime | None = None) -> list[str]:
    """Score every alert not taken and not scored yet whose session's official bars are complete. One line each."""
    now = now or utcnow()
    today = now.astimezone(ET).date()
    since, stale = (today - timedelta(days=LOOKBACK_DAYS)).isoformat(), (today - timedelta(days=STALE_DAYS)).isoformat()
    marks = ",".join("?" * len(NOT_TAKEN))
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            f"SELECT id, symbol, session, posted_at FROM fade_trades WHERE status IN ({marks}) AND wh_net IS NULL AND "  # noqa: S608
            "wh_note IS NULL AND session>=? ORDER BY id LIMIT ?", (*NOT_TAKEN, since, MAX_ROWS))]
    out = []
    for r in rows:
        try:
            if pd.Timestamp(now) < pd.Timestamp(long_book.default_close(r["session"])) + long_book.SIP_DELAY:
                continue                                   # the session's official bars are not complete yet
            line = _one(db, r, bars, cost_fn, r["session"] <= stale)
        except Exception:  # noqa: BLE001 - one alert failing never blocks the others; logged, retried next run
            log.exception("would-have for %s %s failed", r["symbol"], r["id"])
            continue
        if line:
            out.append(line)
    return out


def _put(db: OpsStore, rid: int, **cols) -> bool:
    keys = ", ".join(f"{k}=?" for k in cols)
    with db.tx() as c:                                     # fixed column names; written once, never for a taken trade
        return c.execute(f"UPDATE fade_trades SET {keys} WHERE id=? AND wh_net IS NULL AND wh_note IS NULL AND "  # noqa: S608
                         f"status IN ({','.join('?' * len(NOT_TAKEN))})", (*cols.values(), rid, *NOT_TAKEN)).rowcount == 1


def _one(db: OpsStore, r: dict, bars: DayBars, cost_fn: Cost, stale: bool) -> str | None:
    day = bars(r["symbol"], r["session"])
    if day is None or not len(day):
        return f"{r['symbol']} {NO_BARS}" if stale and _put(db, r["id"], wh_note=NO_BARS) else None    # an outage day is retried
    day = day.assign(ts=pd.to_datetime(day.ts, utc=True))
    rth = day[day.segment == "rth"].sort_values("ts").reset_index(drop=True)
    posted = pd.Timestamp(r["posted_at"])
    posted = posted.tz_localize("UTC") if posted.tzinfo is None else posted.tz_convert("UTC")
    o = short_from(rth, posted + fade.REACTION, r["symbol"], cost_fn, not_after=posted + fade.MAX_ENTRY_DELAY)
    if o is None:
        return f"{r['symbol']} no result" if _put(db, r["id"], wh_note=NO_TRADE) else None
    return f"{r['symbol']} {o['net']:+.1%}" if _put(db, r["id"], wh_entry=o["entry"], wh_net=o["net"]) else None


def gates(db: OpsStore) -> list[dict]:
    """Per choice gate: the stock-days it stopped that have a would-have result, and their average. Each stock-day
    counts once, by its FIRST scored alert: the book takes one fade a stock a day, the first. ``against`` = the
    stopped shorts are winning by more than chance (5+ stock-days and the one-sided 95% lower bound of their average
    above zero; stock-days of one session are treated as independent, which flatters the bound a little). Read-only."""
    with db.tx() as c:
        rows = c.execute("SELECT symbol, session, reason, wh_net FROM fade_trades WHERE wh_net IS NOT NULL AND status IN "  # noqa: S608
                         f"({','.join('?' * len(NOT_TAKEN))}) ORDER BY id LIMIT ?", (*NOT_TAKEN, AUDIT_MAX_ROWS)).fetchall()
    out = []
    for label, tested, prefix in GATES:
        days: dict[tuple[str, str], float] = {}
        for r in rows:                                                   # rows come in id order: the first alert wins
            if (r["reason"] or "").startswith(prefix):
                days.setdefault((r["symbol"], r["session"]), float(r["wh_net"]))
        ev = list(days.values())
        if ev:
            n, avg = len(ev), sum(ev) / len(ev)
            sd = math.sqrt(sum((x - avg) ** 2 for x in ev) / (n - 1)) if n > 1 else math.inf
            out.append({"gate": label, "n": n, "avg": avg, "tested": tested, "since": min(k[1] for k in days),
                        "against": n >= FLAG_MIN and avg - FLAG_Z * sd / math.sqrt(n) > 0})
    return out


def gates_line(db: OpsStore) -> str | None:
    """One line for the nightly summary: what the shorts each gate stopped would have made, against the gate's test.
    A gate whose stopped shorts are winning by more than chance is marked, and at REREAD stock-days it is put to the
    owner; it is never lifted here."""
    g = gates(db)
    if not g:
        return None
    parts = []
    for x in g:
        p = f"{x['gate']} {x['n']} at {x['avg']:+.1%}"
        if x["against"]:
            t = f"tested {x['tested']:+.1%}; " if x["tested"] is not None else ""
            p += f" (AGAINST the gate: {t}{'reread now' if x['n'] >= REREAD else f'reread at {REREAD}'})"
        parts.append(p)
    return f"gates check (shorts not taken, stock-days since {min(x['since'] for x in g)[5:]}, in no total): " + ", ".join(parts)
