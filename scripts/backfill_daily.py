"""Backfill daily bars (split+dividend adjusted) for every listed US equity (swing research).

Active AND delisted symbols (no survivorship bias) from Alpaca, SIP feed, adjustment=all so
returns include dividends and are split-consistent. One Parquet file per symbol under
``--out`` (columns: date, open, high, low, close, volume, vwap, trades). Existing files are
skipped, so the job is resumable.

Usage:
    python scripts/backfill_daily.py --start 2016-01-01 --end 2026-10-16
"""
from __future__ import annotations

import argparse
import logging
import math
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from scan_gappers import _get, active_symbols
from sgt.config import get_settings

log = logging.getLogger("backfill_daily")
_BATCH = 100
_MAX_PAGES = 500


def _rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    recs = []
    for r in rows:
        try:
            rec = {"date": str(r["t"])[:10], "open": float(r["o"]), "high": float(r["h"]),
                   "low": float(r["l"]), "close": float(r["c"]), "volume": float(r["v"]),
                   "vwap": float(r.get("vw", "nan")), "trades": float(r.get("n", "nan"))}
        except (KeyError, TypeError, ValueError):
            continue
        if not all(math.isfinite(rec[k]) and rec[k] > 0 for k in ("open", "high", "low", "close")):
            continue
        recs.append(rec)
    df = pd.DataFrame(recs)
    if not df.empty:
        df = df.drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return df


def fetch_batch(client: httpx.Client, symbols: list[str], start: date, end: date) -> dict[str, list]:
    url = get_settings().alpaca_data_url.rstrip("/") + "/v2/stocks/bars"
    out: dict[str, list] = {}
    token, pages = None, 0
    while True:
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start.isoformat(),
                  "end": end.isoformat(), "adjustment": "all", "feed": "sip", "limit": 10000}
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
            raise SystemExit("daily pagination runaway")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True, type=date.fromisoformat)
    ap.add_argument("--end", required=True, type=date.fromisoformat)
    ap.add_argument("--out", type=Path, default=Path("data/daily"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args.out.mkdir(parents=True, exist_ok=True)

    with httpx.Client(timeout=get_settings().http_timeout_s) as client:
        syms = [s for s in active_symbols(client, include_inactive=True)
                if not (args.out / f"{s}.parquet").exists()]
        log.info("%d symbols to fetch", len(syms))
        written = 0
        for i in range(0, len(syms), _BATCH):
            batch = syms[i:i + _BATCH]
            raw = fetch_batch(client, batch, args.start, args.end)
            for sym in batch:
                df = _rows_to_frame(raw.get(sym, []))
                # an empty file marks "fetched, no data" so a resume does not refetch it
                df.to_parquet(args.out / f"{sym}.parquet", index=False)
                written += int(not df.empty)
            log.info("%d/%d symbols (%d with data)", min(i + _BATCH, len(syms)), len(syms), written)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
