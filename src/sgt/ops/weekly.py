"""Friday weekly review text (owner 2026-11-06: one plain summary of the week; what is learning, what is not).

Pure builder over the ops DB and the nightly report files; the caller sends it once per week.
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from sgt.ops.sms_style import bold, italic
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


def _week_books(db: OpsStore, monday: date, friday: date) -> list[tuple[str, float, float, str]]:
    """(strategy, week dollars, week return, state) per daily book, model books first. state: "marked"; "flat" = no
    mark this week and nothing held ($0: book.settle never marks a book that holds nothing); "unmarked" = it holds
    positions but has no mark this week (a missed job: no dollars invented)."""
    out = []
    with db.tx() as c:
        strategies = [r[0] for r in c.execute("SELECT DISTINCT strategy FROM book_nav ORDER BY strategy")]
        for s in sorted(strategies, key=lambda x: (x.startswith("etf:"), x)):
            rows = c.execute("SELECT session, nav, ret, holdings FROM book_nav WHERE strategy=? AND session<=? "
                             "ORDER BY session",
                             (s, friday.isoformat())).fetchall()
            week = [r for r in rows if r["session"] >= monday.isoformat()]
            if not rows:
                continue
            if not week:
                held = any(abs(w) > 1e-6 for w in json.loads(rows[-1]["holdings"] or "{}").values())
                out.append((s, 0.0, 0.0, "unmarked" if held else "flat"))
                continue
            before = [r for r in rows if r["session"] < monday.isoformat()]
            start = before[-1]["nav"] if before else week[0]["nav"] / (1 + week[0]["ret"])
            out.append((s, week[-1]["nav"] - start, week[-1]["nav"] / start - 1, "marked"))
    return out


def _trade_block(label: str, usd_week: float, nets: pd.Series, symbols: pd.Series, tested: str) -> list[str]:
    from sgt.ops.digest import dot, usd

    if nets.empty:
        return [f"\u26AA {bold(label)} $0, no settled trades this week"]
    wins = int((nets > 0).sum())
    out = [f"{dot(usd_week)} {bold(label)} {usd(usd_week)}, {len(nets)} trades, {wins} won, average "
           f"{_pct(nets.mean())} per trade ({italic('tested ' + tested)})"]
    if len(nets) > 1:
        out.append(f"   best {symbols[nets.idxmax()]} {_pct(nets.max())} \u00B7 worst {symbols[nets.idxmin()]} "
                   f"{_pct(nets.min())}")
    return out


def build(db: OpsStore, friday: date, reports: Path = REPORTS) -> str:
    """2026-11-14 (owner): laid out like the nightly digest: a dollar headline, one line per book with a colour dot,
    sections with bold headers. Every number is this week's (Monday to Friday) unless the line says otherwise."""
    from sgt.fast import fade, long_book
    from sgt.ops.digest import dot, name, usd

    monday = friday - timedelta(days=friday.weekday())
    prior = monday - timedelta(days=1)
    t = week_trades(db, friday)
    with db.tx() as c:
        lt = pd.DataFrame([dict(r) for r in c.execute(
            "SELECT symbol, net FROM long_trades WHERE status='settled' AND net IS NOT NULL AND session BETWEEN ? AND ?",
            (monday.isoformat(), friday.isoformat()))], columns=["symbol", "net"])
    fade_usd = fade.nav(db, friday) - fade.nav(db, prior)
    long_usd = long_book.nav(db, friday) - long_book.nav(db, prior)
    books = _week_books(db, monday, friday)
    models = sum(d for s, d, _, _ in books if not s.startswith("etf:"))
    etf = sum(d for s, d, _, _ in books if s.startswith("etf:"))
    total = models + etf + fade_usd + long_usd
    lines = [f"[SGT] \U0001F5D3\uFE0F {bold('Week of ' + format(monday, '%b %-d'))}: {bold(usd(total))} this week "
             f"{italic('(paper)')}",
             f"models {usd(models)}, ETF {usd(etf)}, fade {usd(fade_usd)}, longs {usd(long_usd)}", "",
             f"\U0001F4B5 {bold('THIS WEEK')}"]
    block = _trade_block("fade", fade_usd, t.net, t.symbol, "+1.1%")
    if not t.empty:
        news = (t.alert_type == "news") | (t.news_own == 1)
        block.insert(1, f"   no-news {int((~news).sum())} avg {_pct(t.net[~news].mean() if (~news).any() else None)} "
                        f"\u00B7 news {int(news.sum())} avg {_pct(t.net[news].mean() if news.any() else None)}")
    lines += block + _trade_block("longs", long_usd, lt.net, lt.symbol, "-2.1%")
    for s, d, r, state in books:
        lines.append(f"{dot(d)} {bold(name(s))} {usd(d)} ({r:+.2%})" if state == "marked" else
                     f"\u26AA {bold(name(s))} $0, {italic('flat')} all week" if state == "flat" else
                     f"\u23F8\uFE0F {bold(name(s))} {italic('no close this week')}")
    switches = [n for n, off in (("fade", fade.is_off(db)), ("longs", long_book.is_off(db))) if off]
    if switches:
        lines.append(f"\u23F8\uFE0F {' and '.join(switches)} {italic('OFF')}: no new trades, still scored every night")
    try:                                              # every book vs its tested expectation (live monitor)
        from sgt.learning import live_monitor

        mon = live_monitor.lines(live_monitor.check_all(db))
        if mon:
            lines += ["", f"\U0001F4CF {bold('LIVE VS TESTED')} {italic('(all trades so far)')}"]
            lines += [f"\U0001F6A8 {m.removeprefix('! ')}" if m.startswith("! ") else f"\u2022 {m}" for m in mon]
    except Exception:  # noqa: BLE001 - one section must never block the weekly text
        lines += ["", "live monitor: unavailable"]
    learn = _line(reports / "learning" / "shadow_summary.json")
    over = _line(reports / "megacap_overlay" / "shadow_summary.json")
    if learn or over:
        lines += ["", f"\U0001F393 {bold('LEARNING')}"]
        if learn:
            lines.append("\u2022 " + learn.removeprefix("learning: "))
        if over:
            lines.append("\u2022 stock model: " + over.replace("megacap overlay fwd ", ""))
    lines += ["", f"\U0001F9ED Decisions come at {DECIDE_N} live trades; one week is noise. Reply STATUS for the books."]
    return "\n".join(lines)
