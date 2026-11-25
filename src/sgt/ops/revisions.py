"""Large revisions (owner 2026-11-24): a trade whose SETTLED result is far from the LIVE estimate that was texted.

GLXG 2026-11-24: the short was texted +4.7% at 14:00 and read -21.2% in the 20:50 report, with nothing that said the
two numbers were the same trade. They are two different records, and neither is a broker fill:

- the LIVE paper path: what the listener did on the free feed (its own entry print, its own stop level, its exit);
- the SETTLED rule replay: the registered rule on the official one-minute bars (for a short, from the first minute
  after alert + 10 s), with its own entry and therefore its own stop level.

A third record, the execution model on real quotes, is written later (21:25 ET) to a research ledger and is reported
on its own line (``sgt.fast.exec_book``): never mixed into this one.

This module puts ONE block in the nightly report (no text of its own) for every trade whose net return moved by
``MIN_POINTS`` or more, or changed sign, between the two records: both entries (time and price), both stop levels,
both exits and reasons, and the cause. A cause is stated only when the two stored records establish it; otherwise
the line says "unresolved". Nothing here changes a trade, a book value or a rule: it only reads.

Order and recovery (the settle runs at 20:40 ET, the report at 20:50):
- a trade of the day that has not settled is listed as "not settled yet", so no revision is implied either way;
- a trade that settles after its own session's report is listed by the next report, once, with its session date.
  "Once" is kept by a watermark (flag ``revisions_through:<day>``: the latest settle time the report of that day
  saw), written by the report job after it has sent. Building the same day's report again gives the same block.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sgt.fast import est_costs
from sgt.fast.fade_rule import ET, STOP
from sgt.ops.sms_style import bold, italic
from sgt.ops.store import OpsStore

log = logging.getLogger(__name__)
MIN_POINTS = 0.05                       # owner 2026-11-24: 5 percentage points of net return, or a change of sign
RETURN_DP = 6                           # the precision returns are compared at (see ``large``)
LOOKBACK_DAYS = 7                       # how far back a late settle or a stuck trade is still listed
MAX_ITEMS = 12                          # a bounded block: more than this is counted, not listed
WATERMARK = "revisions_through"         # flag prefix; value = the latest settled_at the report of that day saw
REPORT_AT = time(20, 50)                # the nightly report (ET): the fallback watermark when no flag exists
UNRESOLVED = "unresolved"
ENTERED = ("open", "est_closed", "settled", "void")
TICK = 1e-6


@dataclass(frozen=True, slots=True)
class Leg:
    """One record of a trade. Every field may be missing: the line then says so instead of guessing."""
    entry_at: str | None = None
    entry: float | None = None
    stop: float | None = None
    exit_at: str | None = None
    exit: float | None = None
    reason: str | None = None
    net: float | None = None


@dataclass(frozen=True, slots=True)
class Item:
    book: str                           # "short" | "long"
    id: int
    symbol: str
    session: str
    status: str
    live: Leg
    settled: Leg | None                 # None while the trade has no settled result
    large: bool                         # the revision the owner asked to see
    cause: str | None
    history: tuple[str, ...] = ()       # earlier live estimates that were themselves corrected (oldest first)
    late: bool = False                  # settled after its own session's report
    note: str | None = None             # how the settled result was made when not the tested way; a void's reason


@dataclass(frozen=True, slots=True)
class Report:
    lines: list[str]
    items: list[Item] = field(default_factory=list)
    through: str | None = None          # the latest settle time this report saw: the next watermark


def large(live: float | None, settled: float | None) -> bool:
    """The owner's rule: the net return moved by MIN_POINTS or more, or changed sign.

    Precision (2026-11-25, explicit; this replaced a ``- 1e-12`` tolerance): both returns are taken at RETURN_DP = 6
    decimal places (a ten-thousandth of a percentage point, far finer than the 0.1 point they are shown at) and the
    difference is then exact decimal arithmetic: +4.7% against -0.3% is 5.0 points and is listed, 4.9999 is not."""
    if live is None or settled is None:
        return False
    a, b = Decimal(repr(round(float(live), RETURN_DP))), Decimal(repr(round(float(settled), RETURN_DP)))
    return abs(b - a) >= Decimal(repr(MIN_POINTS)) or (a > 0 > b) or (a < 0 < b)


def _kind(reason: str | None) -> str:
    """The kind of an exit, so "stop (official tape, seen late)" and "stop" are one exit, "60 min" and "close" one."""
    r = (reason or "").strip().lower()
    if r.startswith("stop"):
        return "stop"
    return "time" if r in ("60 min", "close", "time") else r


def _px(x: float | None) -> str:
    if x is None:
        return "?"
    s = f"{x:.4f}".rstrip("0")
    return "$" + (s + "0" * (2 - len(s.split(".")[1])) if len(s.split(".")[1]) < 2 else s)


def _pct(x: float | None) -> str:
    return "?" if x is None else f"{x:+.1%}"


def _et(at: str | None, seconds: bool) -> str:
    if not at:
        return "?"
    try:
        t = datetime.fromisoformat(str(at))
    except ValueError:
        return "?"
    t = t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
    return t.astimezone(ET).strftime("%H:%M:%S" if seconds else "%H:%M")


def _gap(a: str | None, b: str | None) -> float | None:
    """Seconds from ``a`` to ``b``; None when either cannot be read."""
    try:
        x, y = datetime.fromisoformat(str(a)), datetime.fromisoformat(str(b))
    except (TypeError, ValueError):
        return None
    x = x.replace(tzinfo=timezone.utc) if x.tzinfo is None else x
    y = y.replace(tzinfo=timezone.utc) if y.tzinfo is None else y
    return (y - x).total_seconds()


def _span(s: float) -> str:
    s = int(round(abs(s)))
    return f"{s} s" if s < 60 else f"{s // 60} min {s % 60} s"


def _facts(d: dict) -> dict:
    try:
        f = json.loads(d.get("facts") or "{}")
    except (TypeError, ValueError):
        return {}
    return f if isinstance(f, dict) else {}


def _history(decisions: list[dict]) -> tuple[list[dict], tuple[str, ...]]:
    """(the decision lines that closed the LIVE path, oldest first; a sentence for each close that a later one
    replaced). The row keeps only the last live exit; the decision record keeps every one (append-only)."""
    closes = [d for d in decisions if d.get("state_to") == "est_closed" and d.get("state_from") in ("open", "est_closed")]
    out = []
    for d in closes[:-1]:
        f = _facts(d)
        x = f.get("est_exit", f.get("exit"))
        out.append(f"first texted out {_px(float(x)) if isinstance(x, (int, float)) else '?'} "
                   f"({d.get('reason') or '?'}) at {_et(d.get('at'), True)}, then corrected")
    return closes, tuple(out)


# --- shorts ----------------------------------------------------------------------------------------------------------

def _short(r: dict, decisions: list[dict]) -> tuple[Leg, Leg | None, str | None, tuple[str, ...]]:
    from sgt.fast import fade

    closes, hist = _history(decisions)
    reason, note = r.get("reason"), None
    if str(reason or "").startswith("official"):        # the settle wrote HOW the result was made over the live reason
        note, reason = str(reason), (closes[-1].get("reason") if closes else None)
    entry = r.get("est_entry")
    live = Leg(r.get("est_entry_at"), entry, entry * (1 + STOP) if entry else None, r.get("est_exit_at"),
               r.get("est_exit"), reason,
               est_costs.net("short", r.get("symbol"), r.get("est_entry_at"), entry, r.get("est_exit")))
    if r.get("status") != "settled" or r.get("net") is None:
        return live, None, (str(r.get("reason")) if r.get("status") == "void" else note), hist
    e = r.get("entry")
    settled = Leg(str(r.get("entry_ts")) if r.get("entry_ts") else None, e, e * (1 + STOP) if e else None, None,
                  r.get("cover"), fade.settled_exit(r), r.get("net"))
    return live, settled, note, hist


def _short_cause(live: Leg, settled: Leg, r: dict) -> str:
    if None in (live.entry, live.exit, live.stop, settled.entry, settled.exit, settled.stop):
        return UNRESOLVED
    lk, sk = _kind(live.reason), _kind(settled.reason)
    gap = _gap(settled.entry_at, live.entry_at)
    wait = str(r.get("entry_wait") or "").partition(" ")[2]
    when = ""
    if gap is not None and abs(gap) >= 1:
        when = (f"; the live entry came {_span(gap)} {'after' if gap > 0 else 'before'} the start of the replay's "
                f"entry minute" + (f" ({wait})" if wait else ""))
    if sk == "stop" and lk != "stop":
        if live.stop > settled.stop + TICK:
            return (f"different entries, so different stop levels: official bars reached the replay's "
                    f"{_px(settled.stop)}; the free feed never showed the live path's {_px(live.stop)}" + when)
        return (f"official bars reached the replay's stop {_px(settled.stop)}; the free feed did not show the live "
                f"path's {_px(live.stop)}, which is not higher" + when)
    if lk == "stop" and sk != "stop":
        if live.stop < settled.stop - TICK:
            return (f"different entries, so different stop levels: the free feed showed the live path's "
                    f"{_px(live.stop)}; official bars did not reach the replay's {_px(settled.stop)}" + when)
        return UNRESOLVED
    if lk == sk and lk in ("stop", "time"):
        return (f"the same exit, different prices: entry {_px(live.entry)} live against {_px(settled.entry)}, exit "
                f"{_px(live.exit)} against {_px(settled.exit)}" + when)
    return UNRESOLVED


# --- longs -----------------------------------------------------------------------------------------------------------

def _long(r: dict, decisions: list[dict]) -> tuple[Leg, Leg | None, str | None, tuple[str, ...]]:
    from sgt.fast import long_book, text_order

    closes, hist = _history(decisions)
    reason, note = str(r.get("reason") or ""), None
    if reason == text_order.REASON:                     # booked as the order at the text: the live reason is on record
        note = "settled as the order sent at the text (the tested order did not fill on official bars)"
        kept = [str(d.get("reason") or "") for d in closes]
        reason = next((x.partition("; live exit ")[2] or x for x in reversed(kept) if x and x != text_order.REASON), "")
    elif reason.startswith(long_book.NO_FILL):
        reason = reason.partition("; live exit ")[2]
    live = Leg(r.get("entry_ts"), r.get("est_entry"), r.get("stop"), r.get("est_exit_at"), r.get("est_exit"),
               reason or None, est_costs.net("long", r.get("symbol"), r.get("entry_ts"), r.get("est_entry"), r.get("est_exit")))
    if r.get("status") != "settled" or r.get("net") is None:
        return live, None, (str(r.get("reason")) if r.get("status") == "void" else note), hist
    settled = Leg(r.get("fill_ts"), r.get("entry"), r.get("stop"), r.get("exit_ts"), r.get("exit"), r.get("exit_reason"),
                  r.get("net"))
    return live, settled, note, hist


def _long_cause(live: Leg, settled: Leg) -> str:
    if None in (live.entry, live.exit, settled.entry, settled.exit) or not live.reason or not settled.reason:
        return UNRESOLVED
    lk, sk = _kind(live.reason), _kind(settled.reason)
    if lk != sk:
        return (f"the same rule ended differently on the two feeds: {live.reason} on the free feed's bars, "
                f"{settled.reason} on official bars")
    return (f"the same exit, different prices: entry {_px(live.entry)} live against {_px(settled.entry)}, exit "
            f"{_px(live.exit)} against {_px(settled.exit)}")


# --- the report ------------------------------------------------------------------------------------------------------

def _prior_watermark(c, day: date) -> str:
    """The latest settle time an EARLIER report saw. Without a flag (the first night, or a report that never sent):
    the time the previous weekday's report runs."""
    rows = c.execute("SELECT key, value FROM flags WHERE key LIKE ? AND key < ? ORDER BY key DESC LIMIT 1",
                     (WATERMARK + ":%", f"{WATERMARK}:{day.isoformat()}")).fetchall()
    if rows and rows[0]["value"]:
        return str(rows[0]["value"])
    d = day - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return datetime.combine(d, REPORT_AT, ET).astimezone(timezone.utc).isoformat()


def collect(db: OpsStore, day: date) -> tuple[list[Item], str | None]:
    """Every entered trade of ``day`` plus earlier trades (LOOKBACK_DAYS) that settled after the last report or
    are still not settled; and the latest settle time seen, from the same snapshot."""
    s, since = day.isoformat(), (day - timedelta(days=LOOKBACK_DAYS)).isoformat()
    marks = ",".join("?" * len(ENTERED))
    with db.read() as c:
        mark = _prior_watermark(c, day)
        through = None
        raw = {}
        for book, table in (("short", "fade_trades"), ("long", "long_trades")):
            raw[book] = [dict(r) for r in c.execute(
                f"SELECT * FROM {table} WHERE est_entry IS NOT NULL AND status IN ({marks}) AND (session=? OR "  # noqa: S608
                "(session<? AND session>=? AND (status IN ('open','est_closed') OR settled_at>?))) ORDER BY session, id",
                (*ENTERED, s, s, since, mark))]
            top = c.execute(f"SELECT MAX(settled_at) FROM {table}").fetchone()[0]  # noqa: S608 - fixed names
            through = max(x for x in (through, top) if x) if (through or top) else None
        dec = {(b, r["id"]): [dict(d) for d in c.execute(
            "SELECT at, state_from, state_to, reason, facts FROM decisions WHERE book=? AND ref=? ORDER BY id",
            (b, r["id"]))] for b, rs in raw.items() for r in rs}
    items = []
    for book, rows in raw.items():
        for r in rows:
            try:
                live, settled, note, hist = (_short if book == "short" else _long)(r, dec[(book, r["id"])])
                big = settled is not None and large(live.net, settled.net)
                cause = None
                if r["status"] == "void":
                    big, cause = True, UNRESOLVED                # a texted trade with no settled result at all
                elif settled is not None and live.net is None:
                    big, cause = True, UNRESOLVED                # settled, but the live path never closed on record
                elif big:
                    cause = _short_cause(live, settled, r) if book == "short" else _long_cause(live, settled)
                items.append(Item(book, r["id"], r["symbol"], str(r["session"]), r["status"], live, settled, big, cause,
                                  hist, late=str(r["session"]) < s, note=note))
            except Exception:  # noqa: BLE001 - a reporting block: one bad row is named, never silently dropped
                log.exception("revision line failed for %s %s", book, r.get("id"))
                items.append(Item(book, r.get("id") or 0, str(r.get("symbol")), str(r.get("session")),
                                  str(r.get("status")), Leg(), None, True, "unresolved (the row could not be read)",
                                  late=str(r.get("session")) < s))
    return items, through


def _leg(book: str, leg: Leg, live: bool) -> str:
    open_k = "short" if book == "short" else "buy"
    out = f"out {_et(leg.exit_at, live) + ' ' if leg.exit_at else ''}{_px(leg.exit)}" if leg.exit is not None else \
        "no exit on record"
    return (f"{open_k} {_et(leg.entry_at, live)} {_px(leg.entry)}, stop {_px(leg.stop)}, {out}"
            f"{', ' + leg.reason if leg.reason else ''}")


def _one(i: Item) -> str:
    tag = f" (session {i.session[5:]}, settled late)" if i.late else ""
    if i.status == "void":
        return (f"{i.symbol} {i.book}{tag}: live estimate {_pct(i.live.net)} → no settled result (void: "
                f"{i.note or 'no reason stored'}). Live: {_leg(i.book, i.live, True)}. Cause: {UNRESOLVED}")
    s = i.settled
    hist = f" ({'; '.join(i.history)})" if i.history else ""
    how = f" [{i.note}]" if i.note else ""
    return (f"{i.symbol} {i.book}{tag}: live estimate {_pct(i.live.net)} → settled {_pct(s.net)}. "
            f"Live paper path (free feed): {_leg(i.book, i.live, True)}{hist}. "
            f"Settled rule replay (official bars){how}: {_leg(i.book, s, False)}. Cause: {i.cause or UNRESOLVED}")


def report(db: OpsStore, day: date) -> Report:
    """The block for the nightly report of ``day`` (empty when there is nothing to say) and the next watermark."""
    items, through = collect(db, day)
    big = [i for i in items if i.large]
    waiting = [i for i in items if i.settled is None and i.status in ("open", "est_closed")]
    late = [i for i in items if i.late and i.settled is not None and not i.large]
    lines = []
    if big:
        lines.append(f"✏️ {bold('revised at the settle')} (live estimate → settled result; listed at 5 points or more, or "
                     f"a change of sign; {italic('neither is a broker fill, and the execution model has its own line')}):")
        lines += [f"• {_one(i)}" for i in big[:MAX_ITEMS]]
        if len(big) > MAX_ITEMS:
            lines.append(f"• and {len(big) - MAX_ITEMS} more (see the site)")
    if late:
        lines.append("settled since the last report: " + ", ".join(
            f"{i.symbol} {i.book} ({i.session[5:]}) {_pct(i.settled.net)} (live estimate {_pct(i.live.net)})"
            for i in late[:MAX_ITEMS]))
    if waiting:
        lines.append("⏳ not settled yet, so no revision can be stated: " + ", ".join(
            f"{i.symbol} {i.book}" + (f" ({i.session[5:]})" if i.late else "") + f" live estimate {_pct(i.live.net)}"
            for i in waiting[:MAX_ITEMS]))
    return Report(lines, items, through)


def short_note(db: OpsStore, day: date) -> str | None:
    """One compact line for the midnight summary: the day's own large revisions, by name."""
    items, _ = collect(db, day)
    big = [i for i in items if i.large and not i.late]
    if not big:
        return None
    return "✏️ Revised at the settle (live estimate → settled; details in the 20:50 report): " + ", ".join(
        f"{i.symbol} {_pct(i.live.net)} → {_pct(i.settled.net) if i.settled else 'void'}" for i in big[:MAX_ITEMS])


def mark(db: OpsStore, day: date, through: str | None) -> None:
    """Called by the report job AFTER the report was sent: the next report lists only what settled later."""
    if through:
        db.set_flag(f"{WATERMARK}:{day.isoformat()}", through)
