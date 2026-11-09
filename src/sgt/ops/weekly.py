"""Friday weekly review text (owner 2026-11-06: one plain summary of the week; what is learning, what is not).

Pure builder over the ops DB and the nightly report files; the caller sends it once per week.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from sgt.ops.store import OpsStore

REPORTS = Path("reports")
DECIDE_N = 300


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:+.1%}"


def week_trades(db: OpsStore, friday: date) -> pd.DataFrame:
    monday = friday - timedelta(days=friday.weekday())
    with db.tx() as c:
        rows = c.execute("SELECT symbol, session, alert_type, net, news_own, conf FROM fade_trades "
                         "WHERE status='settled' AND session BETWEEN ? AND ?",
                         (monday.isoformat(), friday.isoformat())).fetchall()
    return pd.DataFrame([dict(r) for r in rows], columns=["symbol", "session", "alert_type", "net", "news_own", "conf"])


def _line(path: Path) -> str | None:
    try:
        line = json.loads(path.read_text()).get("line")
    except (OSError, ValueError):
        return None
    return line if isinstance(line, str) and len(line) <= 300 else None


def build(db: OpsStore, friday: date, reports: Path = REPORTS) -> str:
    t = week_trades(db, friday)
    lines = [f"[SGT] Week of {friday - timedelta(days=friday.weekday()):%b %-d}: weekly review"]
    if t.empty:
        lines.append("Fade: no settled trades this week.")
    else:
        wins = int((t.net > 0).sum())
        news = (t.alert_type == "news") | (t.news_own == 1)
        lines.append(f"Fade: {len(t)} trades, {wins} won, average {_pct(t.net.mean())} per trade "
                     f"(tested record +1.1%).")
        lines.append(f"  news trades {int(news.sum())} avg {_pct(t.net[news].mean() if news.any() else None)}; "
                     f"no-news {int((~news).sum())} avg {_pct(t.net[~news].mean() if (~news).any() else None)}")
        best, worst = t.loc[t.net.idxmax()], t.loc[t.net.idxmin()]
        lines.append(f"  best {best.symbol} {_pct(best.net)}, worst {worst.symbol} {_pct(worst.net)}")
    learn = _line(reports / "learning" / "shadow_summary.json")
    if learn:
        lines.append(learn.replace("learning:", "Learning:", 1))
    over = _line(reports / "megacap_overlay" / "shadow_summary.json")
    if over:
        lines.append("Stock model: " + over.replace("megacap overlay fwd ", ""))
    try:                                              # every book vs its tested expectation (live monitor)
        from sgt.learning import live_monitor

        mon = live_monitor.lines(live_monitor.check_all(db))
        if mon:
            lines += ["Live vs tested:"] + [f"  {m}" for m in mon]
    except Exception:  # noqa: BLE001 - one section must never block the weekly text
        lines.append("live monitor: unavailable")
    lines.append(f"Decisions come at {DECIDE_N} live trades; one week is noise. Reply STATUS for the books.")
    return "\n".join(lines)
