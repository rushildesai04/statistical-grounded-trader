"""The parallel calculation: ``price-decimal-v1`` beside the registered ``binary-v0`` (owner 2026-11-25, option A of
``docs/findings/amendment_stop_arithmetic_2026-11-25.md``).

"Authorize wiring price-decimal-v1 as a parallel calculation with separate fields and explicit version labels.
Preserve binary-v0 as the primary calculation for existing registered cohorts. Do not restate history or mix
versions."

What this module is: for a trade the nightly settle has JUST booked under the registered arithmetic, the same replay
under exact decimal thresholds, returned as values for SEPARATE columns (``*_v1``, ``calc_v1``). What it is not:
- it never feeds a book value, a total, a text, the execution ledger, a review or any registered figure: every one
  of those reads the primary columns, as before (``calc`` says which arithmetic made them: ``binary-v0``);
- it never restates: a trade settled before this existed has no ``*_v1`` values and gets none;
- it never raises into the settle: a value that is not a reported price (more than 6 decimal places, zero, NaN) is a
  visible outcome, ``calc_v1`` = "... not computed (data quality: ...)", counted in the nightly line; it is not
  rounded into a price and it does not stop the job;
- it is not used by the live paper checks (they follow their record's version: ``binary-v0``), the forward record,
  the would-have record, the long shadow, the execution model or any research file. Those consumers are validated
  and enabled separately, if at all.

Wired consumers (2026-11-25): the live short book's settle and the live long book's settle. Nothing else.
"""
from __future__ import annotations

import logging
from datetime import date

import pandas as pd

from sgt.fast import exact_price as xp
from sgt.fast.fade_rule import ET, STOP

log = logging.getLogger(__name__)
VERSION, PRIMARY = xp.VERSION, xp.REGISTERED
SINCE = "2026-11-25"                      # the first session whose settles carry the parallel figure
SAME = f"{VERSION}: same decision as {PRIMARY}"
DIFFERS = f"{VERSION}: DIFFERS from {PRIMARY}: "
NOT_COMPUTED = f"{VERSION}: not computed ("
SHORT_COLS, LONG_COLS = ("cover_v1", "net_v1", "calc_v1"), ("exit_v1", "exit_reason_v1", "net_v1", "calc_v1")


def not_computed(why: str) -> dict:
    return {"calc_v1": (NOT_COMPUTED + str(why))[:280] + ")"}


def _hm(ts) -> str:
    return pd.Timestamp(ts).tz_convert(ET).strftime("%H:%M")


def short(window: pd.DataFrame | None, entry, cost_rt, locate: float) -> dict:
    """The registered short replay's result under ``price-decimal-v1`` for one settled trade. ``window``: the SAME
    official bars the registered replay walked (from its entry bar, 60 minutes, regular hours). Returns the values
    for ``cover_v1`` / ``net_v1`` / ``calc_v1``; ``calc_v1`` alone when it cannot be computed. Never raises."""
    try:
        if window is None or not len(window):
            return not_computed("no official bars for the window")
        w = window.sort_values("ts")
        e, c = float(entry), float(cost_rt)
        got = xp.short_cover(w.high, w.open, w.close, e)
        if got is None:
            return not_computed("no official bars for the window")
        cover, at = float(got[0]), got[1]
        gross = (e - cover) / e                                     # the registered formula, on the v1 cover
        was = next((i for i, h in enumerate(w.high) if xp.binary_short_stop(e, float(h), STOP)), None)
        if was == at:
            note = SAME
        else:
            say = lambda i: "no stop" if i is None else f"stop reached {_hm(w.ts.iloc[i])} ET"  # noqa: E731
            note = f"{DIFFERS}{VERSION} {say(at)}; {PRIMARY} {say(was)}"
        return {"cover_v1": cover, "net_v1": gross - c - float(locate), "calc_v1": note}
    except xp.NotAPrice as exc:
        return not_computed(f"data quality: {exc}")
    except Exception as exc:  # noqa: BLE001 - a parallel figure: its failure is a visible note, never a failed settle
        log.exception("price-decimal-v1 short figure failed")
        return not_computed(f"error: {type(exc).__name__}")


def long(bars: pd.DataFrame, setup, primary, cost_rt, *, latest=None, red_exit: bool = False) -> dict:
    """The registered long replay under ``price-decimal-v1`` for one settled trade: the same simulator, ``exact``
    mode, on the same bars. ``primary``: the Trade the registered run returned. Never raises."""
    from sgt.fast import patterns as pt

    try:
        t = pt.simulate(bars, setup, latest=latest, red_exit=red_exit, exact=True)
        if t is None:
            return not_computed("no fill")
        if (t.reason, t.exit_ts, t.entry) == (primary.reason, primary.exit_ts, primary.entry):
            note = SAME
        else:
            note = (f"{DIFFERS}{VERSION} {t.reason} {_hm(t.exit_ts)} ET; {PRIMARY} {primary.reason} "
                    f"{_hm(primary.exit_ts)} ET")
        return {"exit_v1": float(t.exit), "exit_reason_v1": t.reason, "net_v1": float(t.gross) - float(cost_rt),
                "calc_v1": note}
    except xp.NotAPrice as exc:
        return not_computed(f"data quality: {exc}")
    except Exception as exc:  # noqa: BLE001 - as above
        log.exception("price-decimal-v1 long figure failed")
        return not_computed(f"error: {type(exc).__name__}")


def line(db, day: date | None = None) -> str | None:
    """One line for the nightly report: how many settled trades carry both figures, how many of them the two versions
    decide differently (each named, BOTH results shown, the registered one first), and how many could not be
    computed. None while no trade carries the parallel figure. A read; it changes nothing."""
    with db.read() as c:
        rows = [("short", dict(r)) for r in c.execute(
            "SELECT symbol, session, net, net_v1, calc_v1 FROM fade_trades WHERE status='settled' AND calc_v1 IS NOT NULL "
            "ORDER BY session, id")]
        rows += [("long", dict(r)) for r in c.execute(
            "SELECT symbol, session, net, net_v1, calc_v1 FROM long_trades WHERE status='settled' AND calc_v1 IS NOT NULL "
            "ORDER BY session, id")]
    if not rows:
        return None
    both = [(b, r) for b, r in rows if not str(r["calc_v1"]).startswith(NOT_COMPUTED)]
    diff = [(b, r) for b, r in both if str(r["calc_v1"]).startswith(DIFFERS)]
    skipped = len(rows) - len(both)
    bad = sum("data quality" in str(r["calc_v1"]) for _, r in rows)
    out = (f"arithmetic check since {SINCE[5:]} ({VERSION} beside the registered {PRIMARY}; the registered figure "
           f"decides everything): {len(both)} settled trade{'s' if len(both) != 1 else ''} computed both ways, "
           f"{len(diff)} decided differently")
    if diff:
        out += ": " + ", ".join(
            f"{r['symbol']} {b} {r['session'][5:]} registered {r['net']:+.1%} / {VERSION} {r['net_v1']:+.1%}"
            for b, r in diff[:8]) + (f" and {len(diff) - 8} more" if len(diff) > 8 else "")
    if skipped:
        out += (f"; {skipped} not computed" + (f" ({bad} for a value that is not a reported price)" if bad else ""))
    return out
