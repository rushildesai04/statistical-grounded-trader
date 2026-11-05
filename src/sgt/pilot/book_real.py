"""Real-money 60/40 and turn-of-month at Schwab (design live_pilot_schwab_2026-11-05). Cron ``job pilotbook``.

Each run (every 5 min, 10:00-16:00 ET): sync + reconcile, compute each strategy's target shares, and
- an increase (entry) is first texted for approval (one request per strategy per session, "OK <code>");
- inside EXEC_WINDOW before the close (calendar-aware, half-days included) approved increases and all decreases go
  out as marketable limits split into orders of at most the per-order cap. Unfilled DAY orders expire at the close
  and the next session's run tries again. Every order id is (strategy, session, symbol, chunk): reruns never
  duplicate.
"""
from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Any

from sgt.intraday.calendar import default_calendar
from sgt.ops import approvals, book
from sgt.ops.sms_style import bold
from sgt.ops.store import OpsStore, utcnow
from sgt.pilot import ledger
from sgt.pilot.executor import Pilot, tick

log = logging.getLogger(__name__)

EXEC_WINDOW = timedelta(minutes=15)
CUTOFF = timedelta(minutes=3)
THROUGH = 0.002
MIN_TRADE = 50.0                          # dollars: smaller differences are not worth an order
DRIFT = 0.05                              # 60/40 rebalances when a weight is 5 points off
WEIGHTS_6040 = {"SPY": 0.6, "IEF": 0.4}

Marks = Callable[[list[str]], dict[str, float]]


def _shares(budget: float, weights: dict[str, float], marks: dict[str, float]) -> dict[str, int]:
    budget *= 1 - 2.5 * THROUGH                           # headroom: limits sit 0.2% above the mark
    out = {}
    for s, w in weights.items():
        px = marks.get(s)
        if px is None or not math.isfinite(px) or px <= 0:
            raise ValueError(f"no price for {s}")
        out[s] = int(budget * w // px)
    return out


def _held(db: OpsStore, strategy: str) -> dict[str, int]:
    return {p.symbol: p.qty for p in ledger.positions(db, strategy)}


def target_6040(db: OpsStore, marks: dict[str, float], session: date) -> dict[str, int] | None:
    """None = leave as is. Entry when flat, rebalance on drift or once per new month."""
    held = _held(db, "6040")
    want = _shares(ledger.SUBCAPS["6040"], WEIGHTS_6040, marks)
    if not held:
        return want
    value = {s: held.get(s, 0) * marks[s] for s in WEIGHTS_6040}
    total = sum(value.values())
    drift = total > 0 and any(abs(value[s] / total - w) > DRIFT for s, w in WEIGHTS_6040.items())
    month = session.strftime("%Y-%m")
    if drift or db.flag("pilot_6040_month") != month:
        return want
    return None


def target_tom(db: OpsStore, marks: dict[str, float], session: date) -> dict[str, int] | None:
    """The paper TOM book's SPY weight after this session's close (approved schedule), as real shares."""
    h, _, _ = book.holdings(db, "tom")
    w = dict(h)
    with db.tx() as c:
        for o in c.execute("SELECT symbol, target FROM book_orders WHERE strategy='tom' AND status IN "
                           "('scheduled','filled') AND fill_session<=? ORDER BY fill_session, id",
                           (session.isoformat(),)):
            w[o["symbol"]] = o["target"]
    return _shares(ledger.SUBCAPS["tom"], {"SPY": float(w.get("SPY", 0.0))}, marks)


TARGETS = {"6040": target_6040, "tom": target_tom}


def _diff(held: dict[str, int], want: dict[str, int], marks: dict[str, float]) -> dict[str, int]:
    return {s: want.get(s, 0) - held.get(s, 0) for s in sorted(set(held) | set(want))
            if abs(want.get(s, 0) - held.get(s, 0)) * marks.get(s, 0) >= MIN_TRADE
            or (want.get(s, 0) == 0 and held.get(s, 0))}


def _summary(strategy: str, diff: dict[str, int], marks: dict[str, float]) -> str:
    name = {"6040": "60/40", "tom": "turn-of-month SPY"}[strategy]
    parts = [f"{'buy' if q > 0 else 'sell'} {abs(q)} {s} (~${abs(q) * marks[s]:,.0f})" for s, q in diff.items()]
    return f"💵 {bold('REAL ' + name)} at Schwab, at today's close: " + ", ".join(parts)


async def run(pilot: Pilot, db: OpsStore, send, marks_fn: Marks, now: datetime | None = None) -> list[str]:
    now = now or utcnow()
    session = now.astimezone(approvals.ET).date()
    cal = default_calendar()
    try:
        close = cal.bounds(session).rth_close
    except ValueError:
        return []                                         # not a trading day
    if now >= close - CUTOFF:
        return []
    notes = await pilot.reconcile()
    if notes:
        send(f"[SGT] 🚨 {bold('REAL positions differ from Schwab')}: " + "; ".join(notes)
             + ". New real entries stopped. Check the app, then RESUME.")
    marks = marks_fn(sorted({"SPY", "IEF"} | {p.symbol for p in ledger.positions(db)}))   # total cap values all
    out: list[str] = []
    for strategy, fn in TARGETS.items():
        try:
            out += await _strategy(pilot, db, send, strategy, fn, marks, session, now, close)
        except Exception:  # noqa: BLE001 - one strategy must not block the other; next run retries
            log.exception("pilot %s failed", strategy)
    return out


async def _strategy(pilot: Pilot, db: OpsStore, send, strategy: str, fn, marks: dict[str, float], session: date,
                    now: datetime, close: datetime) -> list[str]:
    want = fn(db, marks, session)
    if want is None:
        return []
    held = _held(db, strategy)
    diff = _diff(held, want, marks)
    if not diff:
        if strategy == "6040":
            db.set_flag("pilot_6040_month", session.strftime("%Y-%m"))
        return []
    buys = {s: q for s, q in diff.items() if q > 0}
    sells = {s: q for s, q in diff.items() if q < 0}
    period = session.isoformat()
    req = approvals.latest(db, f"pilot:{strategy}", period)
    if buys and req is None:
        approvals.request(db, send, strategy=f"pilot:{strategy}", kind="pilot", period=period,
                          summary=_summary(strategy, diff, marks), payload={"strategy": strategy, "diff": diff},
                          expires_at=close - CUTOFF, now=now)
        return [f"{strategy}: asked"]
    if now < close - EXEC_WINDOW:
        return []
    go: dict[str, int] = dict(sells)
    if buys and req is not None and req["status"] == "approved" and approvals.claim(db, req["id"], now):
        approved = {s: int(q) for s, q in json.loads(req["payload"])["diff"].items() if int(q) > 0}
        go.update({s: min(q, approved.get(s, 0)) for s, q in buys.items() if approved.get(s, 0) > 0})
        approvals.finish(db, req["id"], True, "sent to Schwab")
    lines = []
    for sym, q in go.items():
        lines += await _send(pilot, strategy, period, sym, q, marks)
    if lines:
        send(f"[SGT] 💵 {bold('REAL')} {strategy}: " + "; ".join(lines))
    if strategy == "6040" and not buys:
        db.set_flag("pilot_6040_month", session.strftime("%Y-%m"))
    return lines


async def _send(pilot: Pilot, strategy: str, period: str, sym: str, q: int, marks: dict[str, float]) -> list[str]:
    px = marks[sym]
    side = "BUY" if q > 0 else "SELL"
    limit = tick(px * (1 + THROUGH), up=True) if q > 0 else tick(px * (1 - THROUGH), up=False)
    per = max(int(ledger.PER_ORDER // limit), 1)
    left, i, out = abs(q), 0, []
    while left > 0:
        n = min(per, left)
        await pilot.place(strategy=strategy, decision=period.replace("-", ""), leg=f"{sym}{i}", symbol=sym, side=side,
                          qty=n, marks=marks, limit_price=limit)
        out.append(f"{side.lower()} {n} {sym} @ {limit}")
        left -= n
        i += 1
    return out


def ask_reply(row: Any) -> str:
    return "Approved: the REAL order goes to Schwab in the last 15 minutes before today's close."
