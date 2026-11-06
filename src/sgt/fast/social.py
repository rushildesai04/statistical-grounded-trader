"""StockTwits attention at alert time (owner decision 2026-11-06: free StockTwits before the paid X API).

The fade shorts retail-attention spikes; StockTwits' public symbol stream measures that attention directly. The
stream returns the latest 30 messages, so the hotter the symbol the shorter the time those 30 span. Point in time:
only messages created at or before the alert count. Informational and recorded forward (no history exists to test
it on); None = unknown (API down, blocked, malformed), never "no posts".

Fair use: one request per alert (tens per day, far under the public rate limit), generic User-Agent, timeout and a
size bound on every response.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

log = logging.getLogger(__name__)
URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
UA = "statistical-grounded-trader/1.0"
TIMEOUT, MAX_BYTES = 5.0, 2 * 1024 * 1024
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _ts(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def attention(payload: dict, at: datetime) -> dict:
    """Counts from a symbol-stream payload: posts in the last 60 / 15 minutes before ``at`` (of the latest 30),
    minutes the latest 30 span, bullish / bearish tags among the last-hour posts, and the watchlist size."""
    msgs = payload.get("messages") if isinstance(payload, dict) else None
    times, tags = [], []
    for m in msgs if isinstance(msgs, list) else []:
        if not isinstance(m, dict):
            continue
        t = _ts(m.get("created_at"))
        if t is None or t > at:
            continue
        s = ((m.get("entities") or {}).get("sentiment") or {}) if isinstance(m.get("entities"), dict) else {}
        times.append(t)
        tags.append(s.get("basic") if isinstance(s, dict) else None)
    hour = [g for t, g in zip(times, tags) if at - t <= timedelta(hours=1)]
    sym = payload.get("symbol") if isinstance(payload, dict) else None
    watch = sym.get("watchlist_count") if isinstance(sym, dict) else None
    return {"st_n60": len(hour), "st_n15": sum(at - t <= timedelta(minutes=15) for t in times),
            "st_span_min": round((at - min(times)).total_seconds() / 60, 1) if times else None,
            "st_bull": sum(g == "Bullish" for g in hour), "st_bear": sum(g == "Bearish" for g in hour),
            "st_watch": int(watch) if isinstance(watch, (int, float)) else None}


def lookup(symbol: str, at: datetime, client: httpx.Client | None = None) -> dict | None:
    if not _SYMBOL.match(symbol or ""):
        return None
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=False)
    try:
        with client.stream("GET", URL.format(symbol=symbol)) as r:
            if r.status_code != 200:
                log.warning("stocktwits %s -> %s", symbol, r.status_code)
                return None
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    log.warning("stocktwits %s over %d bytes", symbol, MAX_BYTES)
                    return None
        return attention(json.loads(bytes(buf)), at)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("stocktwits %s failed: %s", symbol, type(exc).__name__)
        return None
    finally:
        if own:
            client.close()
