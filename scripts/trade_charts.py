"""Build the Fade Tape chart page (sgt.ops.charts) for the fade book's entered trades.

    python scripts/trade_charts.py --out reports/charts/fade_tape.html            # from the ops database
    python scripts/trade_charts.py --trades trades.jsonl --out fade_tape.html     # from an exported row file

Bars: Alpaca SIP 1-minute, regular session only, ending at least 16 minutes ago (SIP is free once 15 min old).
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from sgt.core.secrets import get_secret
from sgt.fast import fade
from sgt.intraday.calendar import default_calendar
from sgt.ops import charts, model_tape
from sgt.ops.store import OpsStore, utcnow

log = logging.getLogger("trade_charts")

SYMBOL = re.compile(r"[A-Z][A-Z0-9.]{0,9}")
SIP_DELAY = timedelta(minutes=16)
MAX_TRADES = 200
MAX_SESSIONS = 10                                        # the page shows the last 10 trading days
CACHE = Path("reports/charts/bars")


def session_bars(client: httpx.Client, symbol: str, session: str, now: datetime,
                 cache: Path | None = CACHE) -> pd.DataFrame:
    """RTH 1-minute bars; a finished session is cached on disk (it never changes), so the 10-minute rebuild only
    downloads today's symbols."""
    if not SYMBOL.fullmatch(symbol):
        return pd.DataFrame()
    b = default_calendar().bounds(date.fromisoformat(session))
    final = now - SIP_DELAY >= b.rth_close
    path = cache / session / f"{symbol}.parquet" if cache else None
    if final and path and path.exists():
        return pd.read_parquet(path)
    df = _download(client, symbol, b, now)
    if final and path and len(df):
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
    return df


def _download(client: httpx.Client, symbol: str, b, now: datetime) -> pd.DataFrame:
    end = min(b.rth_close, now - SIP_DELAY)
    if end <= b.rth_open:
        return pd.DataFrame()
    r = client.get(f"/v2/stocks/{symbol}/bars", params={
        "timeframe": "1Min", "start": b.rth_open.isoformat(), "end": end.isoformat(), "feed": "sip", "limit": 1000})
    rows = r.json().get("bars") if r.status_code == 200 else None
    if not isinstance(rows, list):
        return pd.DataFrame()
    df = pd.DataFrame([{"ts": x.get("t"), "open": x.get("o"), "high": x.get("h"), "low": x.get("l"),
                        "close": x.get("c"), "volume": x.get("v")} for x in rows if isinstance(x, dict)])
    return df.dropna() if len(df) else df


def from_db(db: OpsStore) -> list[dict]:
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM fade_trades WHERE est_entry IS NOT NULL AND status IN ('open','est_closed','settled') "
            "ORDER BY id DESC LIMIT ?", (MAX_TRADES,))]
    keep = sorted({r["session"] for r in rows}, reverse=True)[:MAX_SESSIONS]
    return [r for r in rows if r["session"] in keep]


def long_rows(db: OpsStore | None) -> list[dict]:
    """Live paper long book trades shaped for charts.trade_view (owner 2026-11-10 option A: longs first). Settled
    longs carry the official entry/exit (the live order replayed on SIP, 2026-11-11); others the live estimate."""
    if db is None:
        return []
    from sgt.fast import long_book
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT t.id, t.symbol, t.session, t.kind, t.status, t.reason, t.stop, t.target, t.weight, t.net, "
            "t.est_entry, t.est_exit, t.est_exit_at, t.entry_ts AS est_entry_at, f.posted_at, f.alert_type, "
            "t.entry, t.exit AS cover, t.fill_ts AS entry_ts, t.exit_ts FROM long_trades t JOIN fade_trades f "
            "ON f.id=t.fade_id WHERE t.status IN ('open','est_closed','settled') ORDER BY t.id DESC LIMIT ?",
            (MAX_TRADES,))]
    keep = sorted({r["session"] for r in rows}, reverse=True)[:MAX_SESSIONS]
    out = []
    for r in rows:
        if r["session"] not in keep:
            continue
        if r["status"] != "settled":                      # the official entry/exit only once settled
            r.update(net=None, entry=None, cover=None, entry_ts=None)
        elif r.get("exit_ts"):
            r["est_exit_at"] = r["exit_ts"]                # the official exit time for the sell marker
        ret = r["net"] if r["net"] is not None else (
            r["est_exit"] / r["est_entry"] - 1 if r.get("est_exit") and r.get("est_entry") else None)
        base = long_book.nav(db, through=date.fromisoformat(r["session"]) - timedelta(days=1))   # value before its day
        out.append({**r, "setup": long_book.NAMES.get(r["kind"], r["kind"]), "size": (r["weight"] or fade.WEIGHT) * base,
                    "usd": None if ret is None else ret * (r["weight"] or fade.WEIGHT) * base})
    return out


def size_usd(t: dict, db: OpsStore | None) -> float:
    """Dollars a fade puts to work: its share of the book value before its day."""
    base = fade.nav(db, through=date.fromisoformat(t["session"]) - timedelta(days=1)) if db else fade.NAV0
    return (t.get("weight") or fade.WEIGHT) * base


def dollars(t: dict, db: OpsStore | None) -> float | None:
    """Paper $ result: the trade's share of the book value before its day (official net where settled)."""
    ret = t.get("net") if t.get("net") is not None else (
        (t["est_entry"] - t["est_exit"]) / t["est_entry"] if t.get("est_entry") and t.get("est_exit") else None)
    if ret is None:
        return None
    base = fade.nav(db, through=date.fromisoformat(t["session"]) - timedelta(days=1)) if db else fade.NAV0
    return ret * (t.get("weight") or fade.WEIGHT) * base


def build(trades: list[dict], db: OpsStore | None, now: datetime, standalone: bool = True) -> str:
    h = {"APCA-API-KEY-ID": get_secret("alpaca_key_id"), "APCA-API-SECRET-KEY": get_secret("alpaca_secret_key")}
    from sgt.ops import score

    views, scan_views, cache = [], [], {}
    fade_score = score.for_trade("fade", None, db=db)    # one lookup: tested, or the forward record in a guard alarm
    with httpx.Client(base_url="https://data.alpaca.markets", headers=h, timeout=10) as client:
        for t in long_rows(db):                          # longs first (owner 2026-11-10 option A)
            key = (t["symbol"], t["session"])
            if key not in cache:
                cache[key] = session_bars(client, *key, now)
            if cache[key].empty:
                continue
            v = charts.trade_view(t, cache[key], t["usd"], side="long")
            v["key"] = f"long:{t['id']}"           # links the chart to its Notes page (journal key)
            v["score"] = score.for_trade("alert_long", t.get("size"))
            views.append(v)
        for t in trades[:MAX_TRADES]:
            key = (t["symbol"], t["session"])
            if key not in cache:
                cache[key] = session_bars(client, *key, now)
            if cache[key].empty:
                continue
            v = charts.trade_view(t, cache[key], dollars(t, db))
            v["key"] = f"fade:{t['id']}" if t.get("id") is not None else None
            v["score"] = score.resize(fade_score, size_usd(t, db))
            views.append(v)
        for t in scan_rows(db):                          # Notes tab charts for scanner trades (owner 2026-11-09)
            key = (t["symbol"], t["session"])
            if key not in cache:
                cache[key] = session_bars(client, *key, now)
            if cache[key].empty:
                continue
            v = charts.trade_view(t, cache[key], None)
            v["key"] = f"scan:{t['id']}"
            scan_views.append(v)
        try:
            model_cards = book_views(db, client, now)
        except Exception:  # noqa: BLE001 - the page still renders without the model-trade cards; logged
            log.exception("book views failed")
            model_cards = []
    board = Path("reports/learning/scoreboard.json")
    try:
        learning = json.loads(board.read_text()).get("rows") if board.exists() else None
    except (OSError, ValueError):
        learning = None                                  # the page still renders without the scoreboard
    models = model_tape.load_for_page(marks=Path("reports/megacap_forward/marks.csv"))   # Models tab (owner 2026-11-06)
    from sgt.ops import journal
    try:
        entries = journal.entries(db) if db is not None else []      # Notes tab (owner 2026-11-09)
    except Exception:  # noqa: BLE001 - the page must render even if the journal query fails; logged
        log.exception("journal entries failed")
        entries = []
    summary = None
    if db is not None:
        from sgt.ops import overview
        summary = overview.build(db, now, learning)      # Overview tab (owner 2026-11-13); sections fail to None
    return charts.render(views, now, standalone=standalone, learning=learning, models=models, journal=entries,
                         scan_views=scan_views, overview=summary, book_views=model_cards)


MAX_BOOK_TRADES = 60
INTRADAY_SESSIONS = 3                                    # a hold up to 3 sessions is drawn on 5-minute bars, longer on daily
SPAN_CACHE = Path("reports/charts/spans")


def span_bars(client: httpx.Client, symbol: str, start: datetime, end: datetime, timeframe: str, now: datetime,
              cache: Path | None = SPAN_CACHE) -> pd.DataFrame:
    """Bars of one timeframe between two instants (bounded: one request, 1,000 bars). A span that ended more than the
    SIP delay ago never changes, so it is cached on disk."""
    if not SYMBOL.fullmatch(symbol) or timeframe not in ("5Min", "1Day"):
        return pd.DataFrame()
    final = now - SIP_DELAY >= end
    stop = min(end, now - SIP_DELAY)
    if stop <= start:
        return pd.DataFrame()
    path = cache / timeframe / f"{symbol}_{start:%Y%m%d}_{end:%Y%m%d}.parquet" if cache else None
    if final and path and path.exists():
        return pd.read_parquet(path)
    r = client.get(f"/v2/stocks/{symbol}/bars", params={
        "timeframe": timeframe, "start": start.isoformat(), "end": stop.isoformat(), "feed": "sip", "limit": 1000,
        "adjustment": "all" if timeframe == "1Day" else "raw"})
    rows = r.json().get("bars") if r.status_code == 200 else None
    if not isinstance(rows, list):
        return pd.DataFrame()
    df = pd.DataFrame([{"ts": x.get("t"), "open": x.get("o"), "high": x.get("h"), "low": x.get("l"),
                        "close": x.get("c"), "volume": x.get("v")} for x in rows if isinstance(x, dict)])
    df = df.dropna() if len(df) else df
    if final and path and len(df):
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(path, index=False)
    return df


def recent_sessions(cal, today: date, n: int) -> list[date]:
    """The last ``n`` sessions up to and including ``today`` (or the last session before it), oldest first.
    (calendar.previous_sessions needs a session date; a weekend rebuild has none.)"""
    last = today
    for _ in range(10):
        if cal.is_session(last):
            break
        last -= timedelta(days=1)
    return [*cal.previous_sessions(last, n - 1), last]


def book_views(db: OpsStore | None, client: httpx.Client, now: datetime, reports: Path = Path("reports")) -> list[dict]:
    """A chart card for every recent daily-book trade (owner 2026-11-14: model and ETF-book trades had no card).
    Recent = entered within the page's last sessions, or still open. One trade's missing bars never drop the rest."""
    if db is None:
        return []
    from sgt.ops import digest, model_trades, score

    cal = default_calendar()
    today = now.astimezone(fade.ET).date()
    sessions = recent_sessions(cal, today, MAX_SESSIONS)
    recent = [d.isoformat() for d in sessions]
    eps = [e for e in model_trades.episodes(db) if e["status"] != "scheduled" and e["entry"]
           and (e["status"] == "open" or e["entry_session"] >= recent[0])]
    out = []
    for e in sorted(eps, key=lambda x: (x["entry_session"], x["id"]), reverse=True)[:MAX_BOOK_TRADES]:
        try:
            first = cal.bounds(date.fromisoformat(e["entry_session"]))
            end_day = date.fromisoformat(e["exit_session"]) if e["exit_session"] else sessions[-1]
            last = cal.bounds(end_day)
            held = len([d for d in recent if e["entry_session"] < d <= end_day.isoformat()])
            tf = "5Min" if e["exit_session"] and held <= INTRADAY_SESSIONS or not e["exit_session"] and held <= 1 else "1Day"
            start = first.rth_open if tf == "5Min" else first.rth_open - timedelta(days=120)
            bars = span_bars(client, e["symbol"], start, last.rth_close, tf, now, cache=SPAN_CACHE)
            if bars.empty:
                continue
            fc = model_trades.forecast(reports, e["strategy"], e["symbol"], e["entry_session"])
            # Money score (owner 2026-11-14): one tested number per side. The per-stock record is NOT shown as an
            # expectation: prereg_record_score_2026-11-14 found it does not predict a stock's next calls.
            sc = score.for_trade(score.kind_of(e["strategy"], e["side"]), abs(e["weight"]) * e["entry_nav"],
                                 sigma=(fc or {}).get("sigma"))
            out.append(charts.book_view(
                e, bars, decided=e["decided_at"], entered=first.rth_close - timedelta(minutes=1),
                exited=last.rth_close - timedelta(minutes=1) if e["exit_session"] else None, timeframe=tf,
                book=digest.name(e["strategy"]), forecast=fc, score=sc))
        except Exception:  # noqa: BLE001 - one trade's card must never take the page down; logged
            log.exception("book view %s %s failed", e.get("strategy"), e.get("symbol"))
    return out


def scan_rows(db: OpsStore | None) -> list[dict]:
    """Recent scanner trades shaped for charts.trade_view (the spike bar plays the alert)."""
    if db is None:
        return []
    with db.tx() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM scan_trades WHERE est_entry IS NOT NULL ORDER BY id DESC LIMIT ?", (MAX_TRADES,))]
    keep = sorted({r["session"] for r in rows}, reverse=True)[:MAX_SESSIONS]
    return [{**r, "posted_at": r["bar_ts"], "alert_type": "scanner spike"} for r in rows if r["session"] in keep]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", type=Path, help="JSON-lines export of fade_trades rows (default: ops database)")
    ap.add_argument("--out", type=Path, default=Path("reports/charts/fade_tape.html"))
    ap.add_argument("--fragment", action="store_true", help="omit the document head (for an Artifact publish)")
    a = ap.parse_args()
    db = None if a.trades else OpsStore()
    trades = [json.loads(x) for x in a.trades.read_text().splitlines() if x.strip()] if a.trades else from_db(db)
    html = build(trades, db, utcnow(), standalone=not a.fragment)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = a.out.with_suffix(".tmp")
    tmp.write_text(html)
    tmp.replace(a.out)                                   # atomic: the chart server never serves a partial page
    print(f"wrote {a.out} ({len(trades)} trades)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
