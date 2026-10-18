"""Backfill consolidated (SIP) 1-minute history from Alpaca into a separate store.

Alpaca's historical SIP bars carry real pre/post-market volume (yfinance reports zero),
and reach back years, so research uses this store as the source of truth. Bars are
kept apart from the yfinance store (default root ``data/alpaca``) so provenance is
never mixed. Sessions already present are skipped; the store is immutable.

Usage:
    python scripts/backfill_alpaca.py --start 2026-07-01 --end 2026-10-16
    python scripts/backfill_alpaca.py --symbols WFF,YMAT --start 2026-01-02 --end 2026-10-16
(default symbols: every tracked symbol from the daily accumulation job + the watchlist)
"""
from __future__ import annotations

import argparse
import logging
import time
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Callable, Iterable, Sequence

from sgt.core.types import Bar
from sgt.db.session import get_session, init_db
from sgt.intraday.calendar import default_calendar
from sgt.intraday.models import TrackedSymbolRow
from sgt.intraday.sources import AlpacaHistoricalBars, DataSourceError
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger("backfill_alpaca")
_UTC = timezone.utc
_CHUNK_DAYS = 14  # one request window per symbol; pagination handles the rest


def tracked_symbols() -> list[str]:
    with get_session() as s:
        return sorted(r.symbol for r in s.query(TrackedSymbolRow).all())


def _windows(start: date, end: date, days: int) -> Iterable[tuple[date, date]]:
    cur = start
    while cur <= end:
        stop = min(end, cur + timedelta(days=days - 1))
        yield cur, stop
        cur = stop + timedelta(days=1)


def backfill(symbols: Sequence[str], start: date, end: date, *, store: ParquetBarStore,
             fetch: Callable[[str, datetime, datetime], list[Bar]],
             sleep: Callable[[float], None] = time.sleep, pacing_s: float = 0.35) -> dict[str, int]:
    """Fetch ``[start, end]`` per symbol in windows and write complete, missing sessions."""
    cal = default_calendar()
    stats = defaultdict(int)
    for sym in symbols:
        have = set(store.sessions(sym))
        for lo, hi in _windows(start, end, _CHUNK_DAYS):
            wanted = [lo + timedelta(days=i) for i in range((hi - lo).days + 1)
                      if cal.is_session(lo + timedelta(days=i))]
            missing = [d for d in wanted if d not in have]
            if not missing:
                continue
            # Free Alpaca plans reject SIP queries reaching into the last 15 minutes (HTTP 403),
            # so the request window never extends past now - 16 minutes.
            req_end = min(datetime.combine(hi + timedelta(days=1), dtime(6, 0), tzinfo=_UTC),
                          datetime.now(_UTC) - timedelta(minutes=16))
            try:
                bars = fetch(sym, datetime.combine(lo, dtime.min, tzinfo=_UTC), req_end)
            except DataSourceError as exc:
                stats["errors"] += 1
                log.warning("fetch failed %s %s..%s: %s", sym, lo, hi, exc)
                sleep(pacing_s)
                continue
            sleep(pacing_s)
            stats["requests"] += 1
            by_session: dict[date, list[Bar]] = defaultdict(list)
            for b in bars:
                by_session[b.session_date].append(b)
            now = datetime.now(_UTC)
            for d in missing:
                session_bars = by_session.get(d)
                if not session_bars:
                    stats["no_data"] += 1
                    continue
                if cal.bounds(d).post_close > now:
                    stats["incomplete_skipped"] += 1  # never store a session still trading
                    continue
                store.write_session(sym, d, sorted(session_bars, key=lambda b: b.ts))
                stats["sessions_written"] += 1
                stats["bars_written"] += len(session_bars)
    return dict(stats)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backfill Alpaca SIP 1-minute history.")
    ap.add_argument("--symbols", default="", help="comma list (default: tracked symbols)")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--data", default="data/alpaca", help="store root (default data/alpaca)")
    ap.add_argument("--pacing", type=float, default=0.35, help="seconds between requests")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    init_db()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] or tracked_symbols()
    if not symbols:
        raise SystemExit("no symbols (run scripts/capture_daily.py first or pass --symbols)")
    src = AlpacaHistoricalBars(feed="sip")
    started = time.time()
    stats = backfill(symbols, date.fromisoformat(args.start), date.fromisoformat(args.end),
                     store=ParquetBarStore(args.data), fetch=src.fetch, pacing_s=args.pacing)
    print(f"symbols={len(symbols)} {stats} in {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
