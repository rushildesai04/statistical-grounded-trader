"""Scan every active US-listed equity for gap-up days (H2 "in play" universe).

Pulls split-adjusted daily bars from Alpaca for all active, tradable NASDAQ/NYSE/AMEX/ARCA
/BATS equities, then flags symbol-days whose official open gapped >= --min-gap over the
previous close inside the price band (sgt.scanner.gappers). Output: one CSV row per gap
day, used to gate the decision dataset to in-play days and to drive the 1-minute backfill.

Usage:
    python scripts/scan_gappers.py --start 2026-07-15 --end 2026-10-16 \
        --out data/universe/gappers.csv
"""
from __future__ import annotations

import argparse
import csv
import logging
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

from sgt.config import get_settings
from sgt.intraday.sources import _alpaca_headers
from sgt.scanner.gappers import find_gap_days, parse_daily_rows

log = logging.getLogger("scan_gappers")

_EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"}
_BATCH = 200
_MAX_PAGES = 200
_RETRIES = 4


def _get(client: httpx.Client, url: str, params: dict[str, Any]) -> Any:
    for attempt in range(_RETRIES + 1):
        try:
            resp = client.get(url, params=params, headers=_alpaca_headers())
        except httpx.TransportError as exc:
            if attempt == _RETRIES:
                raise SystemExit(f"alpaca unreachable: {type(exc).__name__}") from exc
            time.sleep(2 ** attempt)
            continue
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (401, 403):
            raise SystemExit(f"alpaca auth failed: HTTP {resp.status_code}")
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        raise SystemExit(f"alpaca request failed: HTTP {resp.status_code} {resp.text[:200]!r}")
    raise SystemExit("alpaca retries exhausted")


def active_symbols(client: httpx.Client) -> list[str]:
    s = get_settings()
    rows = _get(client, s.alpaca_paper_url.rstrip("/") + "/v2/assets",
                {"status": "active", "asset_class": "us_equity"})
    if not isinstance(rows, list):
        raise SystemExit("assets response is not a list")
    out = []
    for a in rows:
        if not isinstance(a, dict):
            continue
        sym = str(a.get("symbol", ""))
        if (a.get("tradable") and str(a.get("exchange", "")) in _EXCHANGES
                and sym.isalpha() and 1 <= len(sym) <= 5):
            out.append(sym.upper())
    return sorted(set(out))


def daily_bars(client: httpx.Client, symbols: list[str], start: date,
               end: date) -> dict[str, list[dict]]:
    url = get_settings().alpaca_data_url.rstrip("/") + "/v2/stocks/bars"
    out: dict[str, list[dict]] = {}
    token, pages = None, 0
    while True:
        params: dict[str, Any] = {"symbols": ",".join(symbols), "timeframe": "1Day",
                                  "start": start.isoformat(), "end": end.isoformat(),
                                  "adjustment": "split", "feed": "sip", "limit": 10000}
        if token:
            params["page_token"] = token
        payload = _get(client, url, params)
        bars = payload.get("bars") if isinstance(payload, dict) else None
        if not isinstance(bars, dict):
            raise SystemExit("bars response malformed")
        for sym, rows in bars.items():
            if isinstance(rows, list):
                out.setdefault(str(sym).upper(), []).extend(r for r in rows if isinstance(r, dict))
        token = payload.get("next_page_token")
        pages += 1
        if not token:
            return out
        if pages >= _MAX_PAGES:
            raise SystemExit("daily bars pagination runaway")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, type=date.fromisoformat)
    ap.add_argument("--end", required=True, type=date.fromisoformat)
    ap.add_argument("--min-gap", type=float, default=0.10)
    ap.add_argument("--price-min", type=float, default=1.0)
    ap.add_argument("--price-max", type=float, default=30.0)
    ap.add_argument("--out", type=Path, default=Path("data/universe/gappers.csv"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    with httpx.Client(timeout=get_settings().http_timeout_s) as client:
        syms = active_symbols(client)
        log.info("%d active listed equities", len(syms))
        gaps = []
        fetch_start = args.start - timedelta(days=10)   # previous close for the first day
        for i in range(0, len(syms), _BATCH):
            batch = syms[i:i + _BATCH]
            raw = daily_bars(client, batch, fetch_start, args.end)
            for sym, rows in raw.items():
                gaps += find_gap_days(sym, parse_daily_rows(rows), min_gap=args.min_gap,
                                      price_min=args.price_min, price_max=args.price_max,
                                      start=args.start)
            log.info("scanned %d/%d symbols, %d gap days so far",
                     min(i + _BATCH, len(syms)), len(syms), len(gaps))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "session", "prev_close", "open", "gap_pct", "prev_volume"])
        for g in sorted(gaps, key=lambda g: (g.session, g.symbol)):
            w.writerow([g.symbol, g.session.isoformat(), g.prev_close, g.open, g.gap_pct,
                        g.prev_volume])
    days = {g.session for g in gaps}
    log.info("wrote %d gap days (%d symbols, %d sessions) -> %s", len(gaps),
             len({g.symbol for g in gaps}), len(days), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
