"""Approved book (2026-11-02): the owner's paper P&L, holding only what was approved by SMS.

Strategies propose target weights for a fill session (positions change at that session's close).
Exits and reductions apply automatically (blocking an exit amplifies losses; same rule as
sgt.risk.manager); entries, increases and sign flips need an approval before the deadline. The
pre-registered research records are untouched: this book is a separate ledger.

Marking: weights are held constant between fills (daily-rebalanced), close-to-close returns, 5 bp
on turnover at each fill. A missing close blocks that strategy's settlement (fail closed, reported).
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from typing import Callable

import pandas as pd

from sgt.ops import approvals
from sgt.ops.store import OpsStore, utcnow

NAV0 = 100_000.0
COST_BP = 5.0
EPS = 1e-9

ClosesFn = Callable[[list[str], date, date], pd.DataFrame]   # index: session dates, columns: symbols


def holdings(db: OpsStore, strategy: str) -> tuple[dict[str, float], float, str | None]:
    with db.tx() as c:
        r = c.execute("SELECT * FROM book_nav WHERE strategy=? ORDER BY session DESC LIMIT 1", (strategy,)).fetchone()
    return (json.loads(r["holdings"]), r["nav"], r["session"]) if r else ({}, NAV0, None)


def projected(db: OpsStore, strategy: str) -> dict[str, float]:
    """Holdings after every scheduled (not yet filled) order is applied in fill order."""
    h = dict(holdings(db, strategy)[0])
    with db.tx() as c:
        for o in c.execute("SELECT symbol, target FROM book_orders WHERE strategy=? AND status='scheduled' "
                           "ORDER BY fill_session, id", (strategy,)):
            h[o["symbol"]] = o["target"]
    return {s: w for s, w in h.items() if abs(w) > EPS}


def split(cur: dict[str, float], targets: dict[str, float]) -> tuple[dict[str, float], dict[str, float]]:
    """(auto, ask): reductions/exits apply automatically; entries/increases/flips need approval."""
    auto, ask = {}, {}
    for s in sorted(set(cur) | set(targets)):
        h, t = cur.get(s, 0.0), targets.get(s, 0.0)
        if abs(t - h) <= EPS:
            continue
        if abs(t) <= EPS or (h * t > 0 and abs(t) < abs(h)):
            auto[s] = t
        elif h * t < 0:
            auto[s], ask[s] = 0.0, t
        else:
            ask[s] = t
    return auto, ask


def _insert(c, strategy: str, orders: dict[str, float], fill_session: date, source: str,
            approval_id: int | None, label: str) -> None:
    for s, w in orders.items():
        c.execute("INSERT INTO book_orders(strategy, symbol, target, fill_session, source, approval_id, status, "
                  "created_at, label) VALUES (?,?,?,?,?,?,'scheduled',?,?)",
                  (strategy, s, float(w), fill_session.isoformat(), source, approval_id, utcnow().isoformat(), label))


def fmt_targets(t: dict[str, float]) -> str:
    return ", ".join(f"{s} {'flat' if abs(w) <= EPS else ('long' if w > 0 else 'short') + f' {abs(w):.0%}'}"
                     for s, w in t.items())


def propose(db: OpsStore, send, *, strategy: str, targets: dict[str, float], fill_session: date,
            deadline: datetime, label: str, now: datetime | None = None, auto_all: bool = False) -> dict:
    """``auto_all`` (owner decision 2026-11-09 for the model books): every order is scheduled without an approval
    text; the owner is informed. Otherwise entries / increases / flips wait for an SMS approval."""
    now = now or utcnow()
    for s, w in targets.items():
        if not math.isfinite(w) or abs(w) > 1.0:
            raise ValueError(f"{strategy}: target {s}={w} outside [-1, 1]")
    auto, ask = split(projected(db, strategy), targets)
    if auto_all:
        auto, ask = {**auto, **ask}, {}
    with db.tx() as c:
        _insert(c, strategy, auto, fill_session, "auto", None, label)
    aid = None
    if ask:
        aid = approvals.request(db, send, strategy=strategy, kind="book", period=fill_session.isoformat(),
                                summary=f"{label}: enter at {fill_session:%m-%d} close: {fmt_targets(ask)}"
                                        + (f". Auto: {fmt_targets(auto)}" if auto else ""),
                                payload={"targets": ask, "fill_session": fill_session.isoformat(), "label": label},
                                expires_at=deadline, now=now)
    elif auto:
        try:
            send(f"[SGT] {label}: auto at {fill_session:%m-%d} close: {fmt_targets(auto)}")
        except Exception as exc:  # noqa: BLE001 - informational; the orders are already scheduled
            db.audit("notify_failed", strategy=strategy, error=type(exc).__name__)
    db.audit("propose", strategy=strategy, auto=auto, ask=ask, approval_id=aid, fill_session=fill_session)
    return {"auto": auto, "ask": ask, "approval_id": aid}


def apply_approved(db: OpsStore, row, now: datetime | None = None) -> str:
    """Schedule an approved book request's orders (once)."""
    if not approvals.claim(db, row["id"], now):
        return "already handled"
    p = json.loads(row["payload"])
    with db.tx() as c:
        _insert(c, row["strategy"], {s: float(w) for s, w in p["targets"].items()},
                date.fromisoformat(p["fill_session"]), "approved", row["id"], p.get("label", row["strategy"]))
    approvals.finish(db, row["id"], True, f"scheduled for {p['fill_session']} close")
    return f"scheduled for {p['fill_session']} close"


def settle(db: OpsStore, closes: ClosesFn, through: date) -> tuple[list[dict], list[str]]:
    """Mark every strategy through ``through`` (a session whose close is final). Returns (fills, problems)."""
    fills, problems = [], []
    with db.tx() as c:
        strategies = [r[0] for r in c.execute("SELECT DISTINCT strategy FROM book_orders")]
    for strat in strategies:
        h, nav, last = holdings(db, strat)
        with db.tx() as c:
            orders = c.execute("SELECT * FROM book_orders WHERE strategy=? AND status='scheduled' AND fill_session<=? "
                               "ORDER BY fill_session, id", (strat, through.isoformat())).fetchall()
        if not orders and not h:
            continue
        live = []
        for o in orders:
            if last and o["fill_session"] <= last:          # its session is already marked: never back-fill
                with db.tx() as c:
                    c.execute("UPDATE book_orders SET status='void' WHERE id=?", (o["id"],))
                problems.append(f"{strat}: {o['symbol']} order for {o['fill_session']} arrived after marking; voided")
            else:
                live.append(o)
        start = date.fromisoformat(last) if last else min((date.fromisoformat(o["fill_session"]) for o in live),
                                                          default=through)
        syms = sorted(set(h) | {o["symbol"] for o in live})
        px = closes(syms, start - timedelta(days=10), through)
        days = [pd.Timestamp(i).date() for i in px.index]
        px.index = days
        for i, d in enumerate(days):
            if not ((d > start) if last else (d >= start)) or d > through:
                continue
            todays = [o for o in live if o["fill_session"] <= d.isoformat()]   # first close on/after its session
            need = list(h) + [o["symbol"] for o in todays]
            prev = days[i - 1] if i > 0 else None
            if any(s not in px.columns or not (px.at[d, s] > 0) for s in need) or \
                    (h and (prev is None or any(not (px.at[prev, s] > 0) for s in h))):
                problems.append(f"{strat}: missing close on {d} for {need}; settlement stopped")
                break
            r = sum(w * (px.at[d, s] / px.at[prev, s] - 1) for s, w in h.items()) if h else 0.0
            new = dict(h)
            for o in todays:
                new[o["symbol"]] = o["target"]
            new = {s: w for s, w in new.items() if abs(w) > EPS}
            turnover = sum(abs(new.get(s, 0) - h.get(s, 0)) for s in set(new) | set(h))
            nav_new = nav * (1 + r - turnover * COST_BP / 1e4)
            with db.tx() as c:
                c.execute("INSERT INTO book_nav(strategy, session, nav, ret, holdings) VALUES (?,?,?,?,?)",
                          (strat, d.isoformat(), nav_new, nav_new / nav - 1, json.dumps(new)))
                # 2026-11-15: what each held symbol made this session, in the book's own dollars, so a trade card
                # can add up to the book exactly (sgt.ops.model_trades); same transaction as the mark it explains.
                c.executemany("INSERT INTO book_legs(strategy, session, symbol, weight, usd, close) VALUES (?,?,?,?,?,?)",
                              [(strat, d.isoformat(), s, w, w * nav * (px.at[d, s] / px.at[prev, s] - 1),
                                float(px.at[d, s])) for s, w in h.items()])
                for o in todays:
                    c.execute("UPDATE book_orders SET status='filled', fill_price=? WHERE id=?",
                              (float(px.at[d, o["symbol"]]), o["id"]))
                    fills.append({"strategy": strat, "symbol": o["symbol"], "target": o["target"],
                                  "price": float(px.at[d, o["symbol"]]), "session": d.isoformat(),
                                  "source": o["source"], "label": o["label"]})
            live = [o for o in live if o not in todays]
            h, nav, last = new, nav_new, d.isoformat()
    return fills, problems


# Dollars a replayed session may differ from the stored book value. Not zero: a dividend restates every earlier adjusted
# close (rounded), which moved three sessions of the first two weeks by $0.10 to $0.40 on a $100,000 book.
LEGS_TOL = 1.0


def backfill_legs(db: OpsStore, closes: ClosesFn) -> tuple[int, list[str]]:
    """Per-symbol dollars (``book_legs``) for sessions marked before the table existed (2026-11-15), replayed from
    daily closes with the book's own arithmetic. Idempotent: a session that already has its rows is left alone. Fail
    closed: a session whose replay is more than LEGS_TOL from the stored book value writes nothing and is reported
    (its cards keep the older fill-to-fill estimate). Returns (rows written, problems)."""
    written, problems = 0, []
    with db.tx() as c:
        marks: dict[str, list[dict]] = {}
        for r in c.execute("SELECT strategy, session, nav, ret, holdings FROM book_nav ORDER BY strategy, session"):
            marks.setdefault(r["strategy"], []).append(dict(r))
        have = {(r["strategy"], r["session"]) for r in c.execute("SELECT DISTINCT strategy, session FROM book_legs")}
    for strat, rows in marks.items():
        todo = [(rows[i - 1], rows[i]) for i in range(1, len(rows))
                if (strat, rows[i]["session"]) not in have and json.loads(rows[i - 1]["holdings"])]
        if not todo:
            continue
        syms = sorted({s for before, _ in todo for s in json.loads(before["holdings"])})
        first, last = date.fromisoformat(todo[0][0]["session"]), date.fromisoformat(todo[-1][1]["session"])
        px = closes(syms, first - timedelta(days=10), last)
        px.index = [pd.Timestamp(i).date().isoformat() for i in px.index]
        at = {d: i for i, d in enumerate(px.index)}
        for before, row in todo:
            d, h, new = row["session"], json.loads(before["holdings"]), json.loads(row["holdings"])
            i = at.get(d)
            prev = px.index[i - 1] if i else None              # the close before, as settle used (None: no earlier bar)
            if prev is None or row["ret"] <= -1 or any(s not in px.columns or not (px.at[d, s] > 0) or not (px.at[prev, s] > 0) for s in h):
                problems.append(f"{strat}: no closes to replay {d}; its cards keep the estimate")
                continue
            base = row["nav"] / (1 + row["ret"])               # the value the session's returns and costs ran on
            legs = [(strat, d, s, w, w * base * (px.at[d, s] / px.at[prev, s] - 1), float(px.at[d, s]))
                    for s, w in h.items()]
            turnover = sum(abs(new.get(s, 0) - h.get(s, 0)) for s in set(new) | set(h))
            off = base + sum(x[4] for x in legs) - turnover * base * COST_BP / 1e4 - row["nav"]
            if abs(off) > LEGS_TOL:
                problems.append(f"{strat}: replay of {d} does not reproduce the book (${off:+,.2f}); its cards keep the estimate")
                continue
            with db.tx() as c:
                c.executemany("INSERT OR IGNORE INTO book_legs(strategy, session, symbol, weight, usd, close) "
                              "VALUES (?,?,?,?,?,?)", legs)
            written += len(legs)
    return written, problems
