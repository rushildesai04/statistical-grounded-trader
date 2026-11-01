"""Backfill timestamped news headlines from Alpaca (Benzinga) into monthly Parquet files.

Each row: id, created_at (UTC), updated_at, source, symbols (comma-joined), headline.
Months already on disk are skipped (resumable). Bodies are not stored (headlines and
timestamps are what the catalyst study needs; external text is data, never executed).

Usage:
    python scripts/backfill_news.py --start 2024-10 --end 2026-10
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from sgt.config import get_settings
from sgt.intraday.sources import _alpaca_headers

log = logging.getLogger("backfill_news")
_MAX_PAGES = 20_000


def _month_iter(start: str, end: str):
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    while (y, m) <= (ey, em):
        yield y, m
        y, m = (y + (m == 12), 1 if m == 12 else m + 1)


def fetch_month(client: httpx.Client, y: int, m: int) -> pd.DataFrame:
    url = get_settings().alpaca_data_url.rstrip("/") + "/v1beta1/news"
    ny, nm = (y + (m == 12), 1 if m == 12 else m + 1)
    params = {"start": f"{y:04d}-{m:02d}-01T00:00:00Z", "end": f"{ny:04d}-{nm:02d}-01T00:00:00Z",
              "limit": 50, "sort": "asc", "include_content": "false"}
    rows, token, pages = [], None, 0
    while True:
        if token:
            params["page_token"] = token
        for attempt in range(5):
            try:
                r = client.get(url, params=params, headers=_alpaca_headers())
            except httpx.TransportError:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 200:
                break
            if r.status_code in (429,) or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            raise SystemExit(f"news HTTP {r.status_code}: {r.text[:200]!r}")
        else:
            raise SystemExit("news retries exhausted")
        j = r.json()
        for n in j.get("news") or []:
            if not isinstance(n, dict):
                continue
            syms = [str(s).upper() for s in (n.get("symbols") or []) if isinstance(s, str)]
            rows.append({"id": n.get("id"), "created_at": n.get("created_at"),
                         "updated_at": n.get("updated_at"), "source": str(n.get("source", ""))[:40],
                         "symbols": ",".join(syms)[:400], "headline": str(n.get("headline", ""))[:300]})
        token = j.get("next_page_token")
        pages += 1
        if not token or pages >= _MAX_PAGES:
            break
        time.sleep(0.31)            # stay under 200 requests/minute
    return pd.DataFrame(rows)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--out", type=Path, default=Path("data/news"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args.out.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=get_settings().http_timeout_s) as client:
        for y, m in _month_iter(args.start, args.end):
            p = args.out / f"{y:04d}-{m:02d}.parquet"
            if p.exists():
                continue
            df = fetch_month(client, y, m)
            df.to_parquet(p, index=False)
            log.info("%04d-%02d: %d headlines", y, m, len(df))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
