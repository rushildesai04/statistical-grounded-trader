"""Split-adjusted daily bars for the fade event symbols (research input for prereg_price_runner_2026-11-12).

    .venv/bin/python scripts/fetch_adjusted_daily.py      # -> reports/adjusted_daily/bars.parquet (gitignored)

The local data/daily bars are NOT split-adjusted (beta_unknown_study caveat), so a reverse split looks like a +900%
run. This pulls Alpaca 1Day bars with adjustment=split (free SIP history) in batches of 100 symbols, bounded pages,
strict parsing. No secrets are printed.
"""
from __future__ import annotations

import math
import sys
import time
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fade_repeat_days_study as rd  # noqa: E402

from sgt.intraday.sources import _alpaca_headers  # noqa: E402

URL = "https://data.alpaca.markets/v2/stocks/bars"
OUT = Path("reports/adjusted_daily")
START, END, BATCH, MAX_PAGES = "2024-11-01T00:00:00Z", "2026-10-31T00:00:00Z", 25, 400


def parse(sym: str, rows) -> list[dict]:
    out = []
    for r in rows if isinstance(rows, list) else []:
        try:
            c, t = float(r["c"]), str(r["t"])[:10]
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(c) and c > 0 and len(t) == 10:
            out.append({"symbol": sym, "date": t, "close": c})
    return out


def fetch(client: httpx.Client, syms: list[str]) -> list[dict]:
    rows, token = [], None
    for _ in range(MAX_PAGES):
        params = {"symbols": ",".join(syms), "timeframe": "1Day", "start": START, "end": END, "adjustment": "split",
                  "feed": "sip", "limit": 10000}
        if token:
            params["page_token"] = token
        for attempt in range(5):
            try:
                r = client.get(URL, params=params)
            except httpx.TimeoutException:
                time.sleep(2 ** attempt)
                continue
            if r.status_code == 429:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            break
        else:
            raise RuntimeError(f"batch starting {syms[0]} failed after retries")
        j = r.json()
        for sym, rs in (j.get("bars") or {}).items():
            if sym in syms:
                rows += parse(sym, rs)
        token = j.get("next_page_token")
        if not token:
            return rows
    raise RuntimeError(f"page limit hit for batch starting {syms[0]}")


def main() -> int:
    syms = sorted({s for s in rd.load().symbol.astype(str) if s.replace(".", "").isalpha() and len(s) <= 6})
    rows = []
    with httpx.Client(headers=_alpaca_headers(), timeout=90) as client:
        for i in range(0, len(syms), BATCH):
            rows += fetch(client, syms[i:i + BATCH])
            print(f"{min(i + BATCH, len(syms))}/{len(syms)} symbols, {len(rows)} bars", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).drop_duplicates(["symbol", "date"]).to_parquet(OUT / "bars.parquet", index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
