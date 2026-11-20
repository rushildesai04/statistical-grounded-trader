"""Armed long setups: the text that goes out BEFORE the fill (owner 2026-11-18, option 1).

Why: the long book decides on closed 1-minute bars and books a fill only once the FILL bar has closed, so its text
reached the phone 64 to 88 seconds after the booked entry price (live rows 2026-11-11 to 11-17: the price had moved a
median 2.4% by then, against a 2.8% stop distance, and 8 of 14 trades were already over). A setup is known one bar
earlier, when its last candle closes (``Setup.ready``): from that moment the rule's order is fixed. This module texts
that order once, while it can still be acted on. The book itself is unchanged: same fills, same texts after the fill.

One text per (alert, ready time), at most MAX_PER_ALERT per alert, only for an order that is still pending on the
closed bars, that can beat its estimated cost (the book would not take the others), and for a market order only while
it is FRESH. Every armed group is recorded in ``long_armed`` whether texted or not (``note`` says why not), so the
nightly pass (``sgt.fast.text_px``) can price the moment the text went out.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from sgt.fast import est_costs, long_shadow
from sgt.fast import patterns as pt
from sgt.fast.fade import ET
from sgt.ops.sms_style import bold, italic
from sgt.ops.store import OpsStore, iso

FRESH = pd.Timedelta(seconds=90)        # "buy at market now" is only said this soon after the candle closed
MAX_PER_ALERT = 2
Line = tuple[pt.Setup, float]           # a setup and its reference entry price


def pending(f: pd.DataFrame, s: pt.Setup, now: datetime, latest: pd.Timestamp) -> bool:
    """True while the setup's order is alive on the closed bars ``f``: not filled, not cancelled, not expired."""
    at = pd.Timestamp(now)
    if s.ready >= latest or at >= s.expires or at < s.ready:
        return False
    after = f[f.ts >= s.ready]
    if s.trigger is None:
        return after.empty                              # a market order fills on the first bar after the setup
    for b in after.itertuples():
        if b.open <= s.stop or b.high >= s.trigger or b.low <= s.stop:
            return False                                # opened through the stop, triggered, or cancelled
    return True


def reference(f: pd.DataFrame, s: pt.Setup) -> float | None:
    """The entry the order implies: its trigger, or for a market order the close of the setup's last candle."""
    if s.trigger is not None:
        return float(s.trigger)
    before = f[f.ts < s.ready]
    return float(before.close.iloc[-1]) if len(before) else None


def can_win(symbol: str, s: pt.Setup, ref: float) -> bool:
    """False when the 2R target from ``ref`` would not beat the estimated cost (``long_book.no_win``'s rule)."""
    if not ref > s.stop:
        return False
    c = est_costs.cost("long", symbol, s.ready.to_pydatetime(), ref)
    return c is None or pt.RR * (ref - s.stop) / ref > c


def cheap(symbol: str, s: pt.Setup, ref: float) -> bool:
    """False when the estimated cost at ``ref`` is above the long book's cap (``long_book.costly``'s rule): the book
    will not buy that fill, so no armed text goes out for it."""
    from sgt.fast import long_book

    c = est_costs.cost("long", symbol, s.ready.to_pydatetime(), ref)
    return c is None or c <= long_book.COST_CAP


def group(f: pd.DataFrame, row: dict, now: datetime, latest: pd.Timestamp) -> tuple[pd.Timestamp, list[Line], str | None] | None:
    """The earliest pending long setups of this alert (same ``ready``), the lines worth texting, and why none is."""
    from sgt.fast import long_book

    found = long_book.live_setups(f, row)
    live = [s for s in found if s.side == "long" and pending(f, s, now, latest)]
    if not live:
        return None
    ready = min(s.ready for s in live)
    same = sorted((s for s in live if s.ready == ready), key=lambda s: long_book.LONG_KINDS.index(s.kind))
    fresh = pd.Timestamp(now) - ready <= FRESH
    lines, why = [], None
    for s in same:
        ref = reference(f, s)
        if ref is None or not can_win(row["symbol"], s, ref):
            why = why or "cannot beat costs"
        elif not cheap(row["symbol"], s, ref):
            why = why or "cost above the cap"
        elif s.trigger is None and not fresh:
            why = "seen late"
        else:
            lines.append((s, ref))
    return ready, lines, (None if lines else why)


def note(db: OpsStore, row: dict, f: pd.DataFrame, now: datetime, off: bool) -> str | None:
    """Record this alert's armed group once; return its text when one should go out now (None otherwise)."""
    from sgt.fast import long_book

    g = group(f, row, now, long_shadow._latest(row["session"]))
    if g is None:
        return None
    ready, lines, why = g
    with db.tx() as c:
        texted = c.execute("SELECT COUNT(*) FROM long_armed WHERE fade_id=? AND texted_at IS NOT NULL",
                           (row["id"],)).fetchone()[0]
        if lines and texted >= MAX_PER_ALERT:
            lines, why = [], "cap per alert"
        first = lines[0] if lines else None
        new = c.execute(
            "INSERT OR IGNORE INTO long_armed(fade_id, symbol, session, ready, kinds, ref, trigger, stop, texted_at, note, "
            "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["symbol"], row["session"], iso(ready.to_pydatetime()),
             ",".join(s.kind for s, _ in lines) or None, first[1] if first else None,
             next((s.trigger for s, _ in lines if s.trigger is not None), None), first[0].stop if first else None,
             iso(now) if lines else None, why, iso(now))).rowcount == 1
    if not new or not lines:
        return None
    return text(row["symbol"], lines, now, off, long_book.NAMES)


def text(symbol: str, lines: list[Line], now: datetime, off: bool, names: dict[str, str]) -> str:
    """The ARMED card, laid out like the fade's cards (owner 2026-11-20): what to do, the levels, the tested record."""
    ready = lines[0][0].ready
    ago = int((pd.Timestamp(now) - ready).total_seconds())
    out = [f"[SGT] 🟡 {bold('LONG SETUP ARMED')} · {bold(symbol)} · candle closed {ready.tz_convert(ET):%H:%M:%S} ET "
           f"({ago}s ago)"]
    for s, ref in lines:
        risk = (ref - s.stop) / ref
        tgt = ref + pt.RR * (ref - s.stop)
        levels = f"🛑 stop ${s.stop:.2f} ({-risk:.1%}) · 🎯 target ${tgt:.2f} (+{pt.RR * risk:.1%})"
        if s.trigger is None:
            out += [f"💬 {names.get(s.kind, s.kind)}: buy at market now, last ${ref:.2f}", f"{levels} from that price"]
        else:
            out += [f"💬 {names.get(s.kind, s.kind)}: buy-stop ${s.trigger:.2f} · order good until "
                    f"{s.expires.tz_convert(ET):%H:%M} ET", levels]
    out.append("🎲 Tested on past alerts: " + italic(", ".join(
        f"{names.get(s.kind, s.kind)} {long_shadow.TESTED.get(s.kind, 0):+.1%}/trade" for s, _ in lines)))
    out.append("Measure-only (your choice): nothing is traded; a 'seen' text follows if it fills." if off
               else "Paper: a LONG text follows if it fills.")
    return "\n".join(out)
