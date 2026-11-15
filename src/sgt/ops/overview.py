"""Data for the site's Overview tab (owner 2026-11-13: "display all of that information on the site ... making the
confidence score actually make sense").

One honest summary of every book: is it on, what it made, what simply holding made, what its test said to expect, and
for the daily model's calls the tested record of calls like this (win rate and average result per stock and side,
2018-2026 out of sample, `config/models/call_stats.json`). No number here is a prediction or a promise; "tested" and
"so far" are always shown apart. Pure reads: the ops database and files next to it. Any section that cannot be read
is returned as None and the page shows it as unavailable; nothing here raises."""
from __future__ import annotations

import csv
import json
import logging
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sgt.fast import fade, long_book
from sgt.learning import live_monitor
from sgt.ops.store import OpsStore

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
CALL_STATS = Path(__file__).resolve().parents[3] / "config" / "models" / "call_stats.json"
MAX_FILE = 5_000_000
NAV0 = 100_000.0
# book -> (label, group, marks/decisions dir or None, what its test said, plain verdict)
BOOKS = {
    "megacap": ("Megacap model (6 names)", "measure", "megacap_forward",
                "2018-2026: +0.17% per call, 53% winners; failed its bar (Sharpe 0.81 < 1.0); beat holding on no name",
                "forward test, not proven"),
    "megacap_ext": ("Megacap model (15 more names)", "measure", "megacap_ext",
                    "2018-2026 on these names: about +3%/yr, failed", "forward test, not proven"),
    "megacap_long": ("Megacap long calls only (21 names)", "model", None,
                     "2018-2026: +0.23% per long call, 54% winners; +6.4%/yr at Sharpe 0.92 (already-seen data)",
                     "forward test since 11-16, not proven"),
    "megacap_overlay": ("Megacap overlay (hold, step aside on short calls)", "measure", None,
                        "2018-2026: better Sharpe than holding on 14 of 21 names", "shadow test, decided at 300 stock-days"),
    "etf:B_6040": ("60/40 benchmark", "etf", None, "benchmark", "benchmark"),
    "etf:F1_dual_momentum": ("Dual momentum ETFs", "etf", None, "failed its test vs 60/40", "forward comparison"),
    "etf:F2_6040_trend": ("60/40 with trend filter", "etf", None, "failed its test vs 60/40", "forward comparison"),
}


def _f(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _read_marks(p: Path) -> list[dict] | None:
    """Forward marks rows (date, symbol, position, ret, net), voids dropped; None when missing or unreadable."""
    try:
        if not p.exists() or p.stat().st_size > MAX_FILE:
            return None
        with p.open(newline="") as fh:
            return [{"date": r["date"], "symbol": r["symbol"], "position": int(float(r["position"])),
                     "ret": float(r["ret"]), "net": float(r["net"])} for r in csv.DictReader(fh) if r["void"] != "True"]
    except (OSError, KeyError, ValueError, csv.Error):
        log.warning("marks %s unreadable", p)
        return None


def _compound(rows: list[dict], key: str) -> float | None:
    days: dict[str, list[float]] = {}
    for r in rows:
        days.setdefault(r["date"], []).append(r[key])
    v = 1.0
    for d in sorted(days):
        v *= 1 + sum(days[d]) / len(days[d])
    return v - 1 if days else None


def books(db: OpsStore, root: Path) -> list[dict]:
    out = []
    with db.tx() as c:
        for s, (label, group, sub, tested, verdict) in BOOKS.items():
            rows = c.execute("SELECT session, nav, ret, holdings FROM book_nav WHERE strategy=? ORDER BY session",
                             (s,)).fetchall()
            if not rows:
                continue
            marks = _read_marks(root / sub / "marks.csv") if sub else None
            last = rows[-1]
            out.append({"name": label, "group": group, "state": "on", "sessions": len(rows),
                        "since": rows[0]["session"], "last": last["session"], "last_day": _f(last["ret"]),
                        "total": _f(last["nav"] / NAV0 - 1), "nav": _f(last["nav"]),
                        "holding": _compound(marks, "ret") if marks else None,
                        "holdings": json.loads(last["holdings"] or "{}"), "tested": tested, "verdict": verdict})
        n, m = c.execute("SELECT COUNT(*), AVG(net) FROM fade_trades WHERE status='settled'").fetchone()
        ln, lm = c.execute("SELECT COUNT(*), AVG(net) FROM long_trades WHERE status='settled'").fetchone()
    out.append({"name": "Alert fade (shorts)", "group": "alert", "state": "off" if fade.is_off(db) else "on",
                "trades": int(n), "per_trade": _f(m), "total": _f(fade.nav(db) / NAV0 - 1), "nav": _f(fade.nav(db)),
                "tested": "+1.1% per trade for 22 months; November 2026 forward -3.4% per trade",
                "verdict": "off since 11-12 (owner); every alert still scored nightly"})
    out.append({"name": "Alert long book", "group": "alert", "state": "off" if long_book.is_off(db) else "on",
                "trades": int(ln), "per_trade": _f(lm), "total": _f(long_book.nav(db) / NAV0 - 1),
                "nav": _f(long_book.nav(db)),
                "tested": "-2.1% per trade, 14% winners; loses in every tested slice",
                "verdict": "measure-only since 11-13 (owner); the shadow book still scores every alert"})
    return out


def _call_stats(path: Path = CALL_STATS) -> dict:
    try:
        return json.loads(path.read_text()) if path.stat().st_size <= MAX_FILE else {}
    except (OSError, ValueError):
        log.warning("call stats %s unreadable", path)
        return {}


def calls(root: Path, stats: dict | None = None) -> list[dict]:
    """Latest decision of each model set (made at 15:50 ET, filled at the close): every long / short call with the
    tested record of that stock's calls on that side and its forward record so far."""
    stats = _call_stats() if stats is None else stats
    out = []
    for key in ("megacap", "megacap_ext"):
        sub = BOOKS[key][2]
        files = sorted((root / sub / "decisions").glob("*.json")) if (root / sub / "decisions").is_dir() else []
        if not files:
            continue
        try:
            if files[-1].stat().st_size > MAX_FILE:
                raise ValueError("too large")
            dec = json.loads(files[-1].read_text())
            pos = {str(k): v for k, v in dec["positions"].items()}
        except (OSError, ValueError, KeyError, AttributeError, TypeError):
            log.warning("decision %s unreadable", files[-1])
            out.append({"set": BOOKS[key][0], "error": "latest decision unreadable"})
            continue
        marks = _read_marks(root / sub / "marks.csv") or []
        rows = []
        for sym in sorted(pos):
            p = pos[sym] if isinstance(pos[sym], dict) else {}
            side = {1: "long", -1: "short"}.get(int(_f(p.get("position")) or 0))
            if side is None:
                continue
            st = (stats.get(sym) or {}).get(side) or {}
            fw = [m["net"] for m in marks if m["symbol"] == sym and m["position"] == (1 if side == "long" else -1)]
            rows.append({"symbol": sym, "side": side, "pred": _f(p.get("pred")),
                         "tested_n": st.get("n"), "tested_win": _f(st.get("win")), "tested_avg": _f(st.get("avg")),
                         "forward_n": len(fw), "forward_wins": sum(x > 0 for x in fw),
                         "forward_avg": _f(sum(fw) / len(fw)) if fw else None})
        out.append({"set": BOOKS[key][0], "session": str(dec.get("date")), "written_at": str(dec.get("written_at")),
                    "names": len(pos), "flat": len(pos) - len(rows), "calls": rows})
    return out


def fade_record(db: OpsStore) -> dict | None:
    r = live_monitor.check_forward(db)
    if r.get("error") or not r["n"]:
        return None
    return {"n": r["n"], "per_trade": _f(r["mean"]), "win": _f(r["win"]), "tested_per_trade": live_monitor.FORWARD.expectancy,
            "tested_win": live_monitor.FORWARD.win_rate, "alarm": bool(r["alarms"]),
            "size_scale": live_monitor.scale(db, fade.BOOK)}


def build(db: OpsStore, now: datetime, learning: list[dict] | None = None) -> dict:
    root = Path(db.path).resolve().parent.parent
    out: dict = {"asof": now.astimezone(ET).strftime("%a %b %-d, %-I:%M %p ET")}
    for name, fn in (("books", lambda: books(db, root)), ("calls", lambda: calls(root)),
                     ("fade_record", lambda: fade_record(db))):
        try:
            out[name] = fn()
        except Exception:  # noqa: BLE001 - one section failing never blanks the page; logged, shown as unavailable
            log.exception("overview %s failed", name)
            out[name] = None
    rows = [r for r in (learning or []) if isinstance(r, dict) and r.get("challenger")]
    out["leads"] = sorted(({"name": str(r["challenger"]), "study": str(r.get("prereg", "")), "n": int(_f(r.get("n")) or 0),
                            "live": _f(r.get("champ")), "challenger": _f(r.get("chal")), "lower": _f(r.get("lower")),
                            "status": str(r.get("status", ""))} for r in rows),
                          key=lambda r: -(r["lower"] if r["lower"] is not None else -9))
    return out
