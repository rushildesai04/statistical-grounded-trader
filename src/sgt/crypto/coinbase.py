"""Coinbase Exchange public 1-minute candles (no account, no key) -> parquet per product-month.

The response is untrusted: bounded size, strict row validation, timeouts and bounded retries. A minute without a
candle had no trades. Files: ``<root>/<PRODUCT>/<YYYY-MM>.parquet`` with columns ts (UTC), open, high, low, close,
volume, written atomically; an existing month file is complete and never refetched.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd

log = logging.getLogger(__name__)
BASE = "https://api.exchange.coinbase.com"
LEADER = "BTC-USD"
ALTS = ("ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "DOT-USD", "LTC-USD",
        "BCH-USD", "SHIB-USD", "UNI-USD", "NEAR-USD", "AAVE-USD", "XLM-USD", "ATOM-USD", "FIL-USD", "ETC-USD",
        "HBAR-USD")                                  # fixed in prereg_crypto_leadlag_2026-11-04
MAX_ROWS, MAX_BYTES = 300, 200_000                   # API returns at most 300 candles per call
STEP = timedelta(minutes=MAX_ROWS)
PACE_S, RETRIES, TIMEOUT_S = 0.2, 5, 15.0            # 5 req/s, under the public 10 req/s limit
COLS = ["ts", "open", "high", "low", "close", "volume"]


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=TIMEOUT_S, headers={"User-Agent": "sgt-research/1.0"})


def parse(body: bytes, start: datetime, end: datetime) -> pd.DataFrame:
    """Validate one candles response: a list of [time, low, high, open, close, volume] inside [start, end)."""
    if len(body) > MAX_BYTES:
        raise ValueError(f"candles response too large ({len(body)} bytes)")
    import json

    data = json.loads(body)
    if not isinstance(data, list) or len(data) > MAX_ROWS:
        raise ValueError("candles response is not a list of <= 300 rows")
    lo, hi = start.timestamp(), end.timestamp()
    rows, bad = [], 0
    for r in data:
        if not (isinstance(r, list) and len(r) == 6 and all(isinstance(x, (int, float)) for x in r)):
            bad += 1
            continue
        t, low, high, o, c, v = (float(x) for x in r)
        if not (all(math.isfinite(x) for x in (t, low, high, o, c, v)) and lo <= t < hi and t % 60 == 0
                and 0 < low <= min(o, c) and max(o, c) <= high and v >= 0):
            bad += 1
            continue
        rows.append((int(t), o, high, low, c, v))
    if bad:
        log.warning("dropped %d invalid candles", bad)
    df = pd.DataFrame(rows, columns=COLS)
    df["ts"] = pd.to_datetime(df.ts, unit="s", utc=True)
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def fetch(c: httpx.Client, product: str, start: datetime, end: datetime, sleep=time.sleep) -> pd.DataFrame:
    """Candles in [start, end) in 300-minute calls, paced, with bounded retries on 429 / 5xx / network errors."""
    out, t = [], start
    while t < end:
        u = min(t + STEP, end)
        for attempt in range(RETRIES):
            try:
                r = c.get(f"/products/{product}/candles", params={
                    "granularity": 60, "start": t.isoformat(), "end": (u - timedelta(minutes=1)).isoformat()})
            except httpx.TransportError as exc:
                log.warning("%s %s network error %s (attempt %d)", product, t, type(exc).__name__, attempt + 1)
                sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                sleep(2 ** attempt)
                continue
            r.raise_for_status()
            out.append(parse(r.content, t, u))
            break
        else:
            raise RuntimeError(f"{product} candles {t:%Y-%m-%d %H:%M} failed after {RETRIES} attempts")
        sleep(PACE_S)
        t = u
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=COLS)


def month_path(root: Path, product: str, month: str) -> Path:
    return root / product / f"{month}.parquet"


def fetch_month(c: httpx.Client, root: Path, product: str, month: str, sleep=time.sleep) -> Path | None:
    """Fetch one UTC month unless already stored. Returns the path written, or None if it existed."""
    p = month_path(root, product, month)
    if p.exists():
        return None
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc)
    end = (start + timedelta(days=32)).replace(day=1)
    df = fetch(c, product, start, end, sleep)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(p)
    return p


def load(root: Path, product: str, months: list[str]) -> pd.DataFrame:
    parts = [pd.read_parquet(month_path(root, product, m)) for m in months if month_path(root, product, m).exists()]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=COLS)
