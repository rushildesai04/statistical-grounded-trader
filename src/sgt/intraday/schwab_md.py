"""Schwab market data, read-only: 1-minute candles (owner 2026-11-18, decision B: "connect up Schwab and keep the
Alpaca for now").

Why: the live books read the free Alpaca feed, which is one exchange. On 2026-11-18 it carried 2.0%, 0.2% and 0.5%
of the real volume of the three stocks the long book watched; its candles are not the real candles (a red candle's
low of 7.16 against 7.13 on the full tape stopped a long that the rule on real candles won). Official full-tape bars
are free only 15 minutes late. A Schwab account's market data is the candidate for full-tape candles in real time
at no cost.

Status: NOT wired into any book. ``scripts/schwab_md_check.py`` first has to show, on a real login, that these
candles (a) match the official tape and (b) arrive in real time (``agreement`` / ``freshness`` below). The request
shape follows Schwab's Trader API "Market Data Production" product as documented for ``/marketdata/v1/pricehistory``
and is confirmed only by that check [VERIFY on first login].

Boundaries (external bytes are hostile): the symbol is validated and sent as a query parameter, never built into a
path; one bounded request with the client's timeout; the payload is parsed strictly and capped at MAX_CANDLES; any
failure returns None so a caller can fall back to its other feed. The bearer token is never logged. This module never
refreshes a token: the caller supplies one (the single token writer stays ``SchwabTokenManager``).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import httpx
import pandas as pd

log = logging.getLogger(__name__)
PATH = "/marketdata/v1/pricehistory"
MAX_CANDLES = 5_000                       # one day of minutes with extended hours is 960
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
COLUMNS = ["ts", "open", "high", "low", "close", "volume"]
CENT = 0.0101                              # two prices "agree" within a cent
FULL_TAPE_VOLUME, FULL_TAPE_MATCH, FRESH_S = 0.90, 0.95, 120.0


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def parse_candles(payload) -> pd.DataFrame:
    """Rows of a price-history payload that are whole, numeric candles (ts = bar start, UTC), time-ordered."""
    rows = payload.get("candles") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return pd.DataFrame(columns=COLUMNS)
    out = []
    for x in rows[:MAX_CANDLES]:
        if not isinstance(x, dict) or not all(_num(x.get(k)) for k in ("open", "high", "low", "close", "volume", "datetime")):
            continue
        o, h, lo, c, v, ms = (float(x[k]) for k in ("open", "high", "low", "close", "volume", "datetime"))
        if min(o, h, lo, c) <= 0 or h < lo or v < 0 or not 0 < ms < 4e12:
            continue
        out.append((pd.Timestamp(int(ms), unit="ms", tz="UTC"), o, h, lo, c, v))
    f = pd.DataFrame(out, columns=COLUMNS)
    return f.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)


async def price_history(http: httpx.AsyncClient, token: str, symbol: str, start: datetime, end: datetime,
                        *, extended: bool = True) -> pd.DataFrame | None:
    """1-minute candles of ``symbol`` in [start, end]. None on any failed request (the caller falls back)."""
    if not isinstance(symbol, str) or not SYMBOL.match(symbol):
        raise ValueError("not a plain stock symbol")
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("start and end must be timezone-aware and ordered")
    params = {"symbol": symbol, "periodType": "day", "frequencyType": "minute", "frequency": 1,
              "startDate": int(start.astimezone(timezone.utc).timestamp() * 1000),
              "endDate": int(end.astimezone(timezone.utc).timestamp() * 1000),
              "needExtendedHoursData": "true" if extended else "false", "needPreviousClose": "false"}
    try:
        r = await http.get(PATH, params=params, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    except httpx.HTTPError as exc:
        log.warning("schwab price history %s: %s", symbol, type(exc).__name__)
        return None
    if r.status_code != 200:
        log.warning("schwab price history %s: HTTP %s", symbol, r.status_code)      # status only: never the body or token
        return None
    try:
        return parse_candles(r.json())
    except ValueError:
        log.warning("schwab price history %s: not JSON", symbol)
        return None


def agreement(schwab: pd.DataFrame, tape: pd.DataFrame) -> dict:
    """How well Schwab's candles match the official tape's, minute by minute (both: ts, high, low, volume)."""
    if not len(tape):
        return {"tape_minutes": 0, "both": 0, "full_tape": False}
    m = tape.merge(schwab, on="ts", how="left", suffixes=("_tape", "_schwab"))
    both = m.dropna(subset=["high_schwab"])
    if not len(both):
        return {"tape_minutes": int(len(tape)), "both": 0, "missing_in_schwab": int(len(tape)), "full_tape": False}
    hi = float(((both.high_schwab - both.high_tape).abs() <= CENT).mean())
    lo = float(((both.low_schwab - both.low_tape).abs() <= CENT).mean())
    vol = float(both.volume_schwab.sum() / both.volume_tape.sum()) if both.volume_tape.sum() > 0 else 0.0
    return {"tape_minutes": int(len(tape)), "both": int(len(both)), "missing_in_schwab": int(len(tape) - len(both)),
            "high_match": hi, "low_match": lo, "volume_share": vol,
            "full_tape": bool(vol >= FULL_TAPE_VOLUME and min(hi, lo) >= FULL_TAPE_MATCH
                              and len(both) >= FULL_TAPE_MATCH * len(tape))}


def freshness(schwab: pd.DataFrame, now: datetime) -> dict:
    """Age of the newest candle's END. Real time = the minute that just closed is already there."""
    if not len(schwab):
        return {"age_s": None, "real_time": False}
    age = (pd.Timestamp(now) - (schwab.ts.iloc[-1] + pd.Timedelta(minutes=1))).total_seconds()
    return {"age_s": float(age), "real_time": bool(age <= FRESH_S)}
