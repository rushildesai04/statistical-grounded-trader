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
    views, cache = [], {}
    with httpx.Client(base_url="https://data.alpaca.markets", headers=h, timeout=10) as client:
        for t in trades[:MAX_TRADES]:
            key = (t["symbol"], t["session"])
            if key not in cache:
                cache[key] = session_bars(client, *key, now)
            if cache[key].empty:
                continue
            views.append(charts.trade_view(t, cache[key], dollars(t, db)))
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
    return charts.render(views, now, standalone=standalone, learning=learning, models=models, journal=entries)


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
