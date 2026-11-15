"""Daily-book positions as trades (owner 2026-11-14: "regardless of if its any alert or an alpha or something else we
pick up on, it should be entered as a trade with a card and its own page").

The daily books (the megacap model sets, the long-calls book, the ETF books) store target weights per day
(``book_orders``), not trades. A trade here is one position from the close it was entered at to the close it was
exited at (target back to zero, or the side flipped): what a trader would call the trade. Pure over the ops DB plus two
small files; no network. Prices are the official closes the book itself filled at.
"""
from __future__ import annotations

import json
from pathlib import Path

from sgt.ops.book import COST_BP, EPS, NAV0
from sgt.ops.store import OpsStore

MAX_FILE = 2_000_000                                    # decision / stats files are ~20 KB; bound the read
DECISION_DIRS = {"megacap": ("megacap_forward",), "megacap_ext": ("megacap_ext",),
                 "megacap_long": ("megacap_forward", "megacap_ext")}


def _sign(x: float) -> int:
    return 1 if x > EPS else -1 if x < -EPS else 0


def _new(o: dict, nav: float, basis: float) -> dict:
    side = _sign(o["target"])
    return {"key": f"book:{o['id']}", "id": o["id"], "strategy": o["strategy"], "symbol": o["symbol"],
            "side": "long" if side > 0 else "short", "weight": o["target"], "entry_session": o["fill_session"],
            "entry": o["fill_price"], "decided_at": o["created_at"], "exit_session": None, "exit": None,
            "status": "open", "sessions": 0, "gross": None, "net": None, "entry_nav": nav,
            "usd": -abs(o["target"]) * basis * COST_BP / 1e4,        # the entry's share of the book's trading cost
            "_w": o["target"], "_px": o["fill_price"], "_at": o["fill_session"]}


def _netted(orders: list[dict]) -> list[dict]:
    """One order per (book, symbol, session). The book applies a session's fills for a symbol in order and holds only
    the last target, paying one turnover from where it stood (``book.settle``); a card must not pay for targets the
    book never held (audit 2026-11-15: NVDA 11-09 had exit, short, smaller short in one session: $2.96 too much).
    The group keeps the last target and the identity (id, decision time) of the order that began its final side, so
    a card's key does not move. Orders waiting for the close are netted the same way (the book will hold only the
    last of them), never together with filled ones. ``orders`` sorted by book, symbol, session, id."""
    out: list[dict] = []
    for o in orders:
        last = out[-1] if out else None
        if last is not None and (last["strategy"], last["symbol"], last["fill_session"], last["status"]) == (
                o["strategy"], o["symbol"], o["fill_session"], o["status"]):
            lead = last if _sign(last["target"]) == _sign(o["target"]) else o
            out[-1] = {**lead, "target": o["target"]}
        else:
            out.append(o)
    return out


def episodes(db: OpsStore) -> list[dict]:
    """Every position of every daily book, oldest first: closed, open, and scheduled (ordered, not filled yet).

    Dollars follow the book (``book.settle``): a trade earns what the book booked for its symbol on every session it
    was held (``book_legs``: constant weight on the book's value before that day's mark), and EVERY change of weight
    (entry, resize, exit) pays the book's cost on its own turnover, on the same value; several orders for one symbol
    in one session count as the one change the book made (``_netted``). So the trades of a book add up to the book's
    own change. A resize keeps the trade open at the new weight. A stretch with a session the book has
    no row for (marked before 2026-11-15 and not backfilled) falls back to weight x book value x the price change
    between its two fills: shares held, a few dollars off the book over a multi-session hold.
    Audit 2026-11-15: the resize paid nothing and costs ran on the day's closing value; then every multi-session
    stretch was priced fill to fill, so the ETF books' cards read $38 to $57 below their books."""
    with db.tx() as c:
        orders = [dict(r) for r in c.execute(
            "SELECT id, strategy, symbol, target, fill_session, status, fill_price, created_at FROM book_orders "
            "WHERE status IN ('filled','scheduled') ORDER BY strategy, symbol, fill_session, id")]
        navs: dict[tuple[str, str], float] = {}
        before: dict[tuple[str, str], float] = {}        # book value before a session's mark: what its costs ran on
        for r in c.execute("SELECT strategy, session, nav, ret FROM book_nav"):
            navs[(r["strategy"], r["session"])] = r["nav"]
            before[(r["strategy"], r["session"])] = (r["nav"] / (1 + r["ret"])
                                                     if r["ret"] is not None and r["ret"] > -1 else r["nav"])
        days: dict[str, list[str]] = {}
        for r in c.execute("SELECT strategy, session FROM book_nav ORDER BY session"):
            days.setdefault(r["strategy"], []).append(r["session"])
        legs: dict[tuple[str, str], dict[str, tuple[float, float]]] = {}   # (strategy, symbol) -> session -> (usd, close)
        for r in c.execute("SELECT strategy, session, symbol, usd, close FROM book_legs"):
            legs.setdefault((r["strategy"], r["symbol"]), {})[r["session"]] = (r["usd"], r["close"])

    def booked(e: dict, strategy: str, through: str | None) -> list[tuple[float, float]] | None:
        """The book's rows for this trade's symbol on every marked session after its last fill, up to ``through``
        (None: to the last mark). None when there is no such session or one of them has no row."""
        mine = legs.get((strategy, e["symbol"]), {})
        held = [d for d in days.get(strategy, []) if d > e["_at"] and (through is None or d <= through)]
        return [mine[d] for d in held] if held and all(d in mine for d in held) else None
    out: list[dict] = []
    cur: dict | None = None
    pair = None
    for o in _netted(orders):
        if (o["strategy"], o["symbol"]) != pair:
            if cur is not None:
                out.append(cur)
            cur, pair = None, (o["strategy"], o["symbol"])
        nav = navs.get((o["strategy"], o["fill_session"]), NAV0)
        basis = before.get((o["strategy"], o["fill_session"]), NAV0)
        side = _sign(o["target"])
        if o["status"] == "scheduled":                   # ordered at 15:50, fills at the close
            if cur is None and side:
                out.append({**_new(o, nav, basis), "status": "scheduled", "entry": None, "usd": None})
            elif cur is not None and side != _sign(cur["_w"]):
                cur["exit_scheduled"] = o["fill_session"]
            continue
        px = o["fill_price"]
        if cur is not None and px and cur["_px"]:
            rows = booked(cur, o["strategy"], o["fill_session"])
            cur["usd"] += (sum(u for u, _ in rows) if rows is not None else
                           cur["_w"] * navs.get((o["strategy"], cur["_at"]), NAV0) * (px / cur["_px"] - 1))
            cur["_px"], cur["_at"] = px, o["fill_session"]
            if side != _sign(cur["_w"]):                 # back to zero, or flipped: the trade is over
                s = _sign(cur["_w"])
                cur["usd"] -= abs(cur["_w"]) * basis * COST_BP / 1e4
                cur.update(exit_session=o["fill_session"], exit=px, status="closed",
                           gross=s * (px / cur["entry"] - 1), net=s * (px / cur["entry"] - 1) - 2 * COST_BP / 1e4)
                out.append(cur)
                cur = None
            else:                                        # same side, new weight: the book charged this turnover too
                cur["usd"] -= abs(o["target"] - cur["_w"]) * basis * COST_BP / 1e4
                cur["_w"] = o["target"]
        if cur is None and side and px:
            cur = _new(o, nav, basis)
    if cur is not None:
        out.append(cur)
    for e in out:                                        # an open trade: the sessions the book marked since its last fill
        rows = booked(e, e["strategy"], None) if e["status"] == "open" else None
        if rows:
            e["usd"] += sum(u for u, _ in rows)
            e["_px"], e["_at"] = rows[-1][1], days[e["strategy"]][-1]   # a live estimate runs on from this close
    for e in out:
        held = [d for d in days.get(e["strategy"], []) if d > e["entry_session"]
                and (e["exit_session"] is None or d <= e["exit_session"])]
        e["sessions"] = len(held)
        e["last_px"], e["last_at"], e["last_w"] = e.pop("_px"), e.pop("_at"), e.pop("_w")
        e["nav"] = navs.get((e["strategy"], e["last_at"]), NAV0)    # book value the open leg is running on
    return sorted(out, key=lambda e: (e["entry_session"], e["id"]))


def _read(path: Path) -> dict | None:
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            return None
        got = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return got if isinstance(got, dict) else None


def forecast(reports: Path, strategy: str, symbol: str, session: str) -> dict | None:
    """The model's forecast behind an entry: next-day move it expected and its no-trade band (None for books that
    are not the daily model, or when the decision file is missing or malformed)."""
    for sub in DECISION_DIRS.get(strategy, ()):
        dec = _read(reports / sub / "decisions" / f"{session}.json")
        pos = (dec or {}).get("positions")
        p = pos.get(symbol) if isinstance(pos, dict) else None
        if isinstance(p, dict) and isinstance(p.get("pred"), (int, float)):
            out = {k: float(p[k]) for k in ("pred", "q_lo", "q_hi") if isinstance(p.get(k), (int, float))}
            f = p.get("features")
            if isinstance(f, dict) and isinstance(f.get("d_rv21"), (int, float)) and f["d_rv21"] > 0:
                out["sigma"] = float(f["d_rv21"])                # the stock's daily swing at the decision
            return out
    return None


def tested(stats: dict | None, symbol: str, side: str) -> dict | None:
    """The backtest record of calls like this one (same stock, same side): n, win rate, average net per call."""
    s = ((stats or {}).get(symbol) or {}).get(side) if isinstance((stats or {}).get(symbol), dict) else None
    if not isinstance(s, dict) or not all(isinstance(s.get(k), (int, float)) for k in ("n", "win", "avg")):
        return None
    return {"n": int(s["n"]), "win": float(s["win"]), "avg": float(s["avg"])}
