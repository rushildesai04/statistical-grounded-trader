"""X (Twitter) attention at alert time (owner decision 2026-11-08: add the X API next to StockTwits).

Recent search (API v2) for the symbol's cashtag over the hour before the alert: post count (last 60 / 15 min), unique
authors and total engagement. Point in time: ``end_time`` = the alert. Pay-per-use API, so every call is bounded: one
request per alert, at most 100 posts, and a daily request cap counted in the ops DB. Dormant (returns None) until the
bearer token secret ``x_bearer`` exists; None also means unknown (error, cap reached), never "no posts".
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from sgt.core.secrets import get_secret

log = logging.getLogger(__name__)
URL = "https://api.x.com/2/tweets/search/recent"
TIMEOUT, MAX_BYTES, DAILY_CAP = 6.0, 1024 * 1024, 60
END_LAG = timedelta(seconds=15)
_SYMBOL = re.compile(r"^[A-Z]{1,6}$")
_CALLS: dict[str, int] = {}                      # UTC date -> requests made by this process (the listener is one process)


def _ts(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def attention(payload: dict, at: datetime) -> dict:
    """Counts from a recent-search payload (posts created at or before ``at``)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = []
    for p in data if isinstance(data, list) else []:
        if not isinstance(p, dict):
            continue
        t = _ts(p.get("created_at"))
        if t is None or t > at:
            continue
        m = p.get("public_metrics") if isinstance(p.get("public_metrics"), dict) else {}
        eng = sum(int(m.get(k) or 0) for k in ("like_count", "retweet_count", "reply_count", "quote_count")
                  if isinstance(m.get(k), (int, float)))
        rows.append((t, str(p.get("author_id") or ""), eng))
    return {"x_n60": sum(at - t <= timedelta(hours=1) for t, _, _ in rows),
            "x_n15": sum(at - t <= timedelta(minutes=15) for t, _, _ in rows),
            "x_authors": len({a for _, a, _ in rows if a}), "x_eng": sum(e for _, _, e in rows)}


def lookup(symbol: str, at: datetime, client: httpx.Client | None = None) -> dict | None:
    """X attention for ``symbol`` in the hour before ``at``; None = dormant, capped or unknown."""
    day = at.astimezone(timezone.utc).date().isoformat()
    if not _SYMBOL.match(symbol or "") or _CALLS.get(day, 0) >= DAILY_CAP:
        return None
    token = get_secret("x_bearer", required=False)
    if not token:
        return None
    _CALLS[day] = _CALLS.get(day, 0) + 1                # counted before the call: a failed call still costs
    own = client is None
    client = client or httpx.Client(headers={"Authorization": f"Bearer {token}", "User-Agent": "statistical-grounded-trader/1.0"},
                                    timeout=TIMEOUT, follow_redirects=False)
    params = {"query": f"${symbol} -is:retweet", "max_results": 100, "tweet.fields": "created_at,author_id,public_metrics",
              "start_time": (at - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end_time": (at - END_LAG).strftime("%Y-%m-%dT%H:%M:%SZ")}   # X refuses end_time < 10 s before now
    try:
        r = client.get(URL, params=params)
        if r.status_code != 200 or len(r.content) > MAX_BYTES:
            log.warning("x search %s -> %s", symbol, r.status_code)       # never log the token or the URL params
            return None
        return attention(r.json(), at)
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("x search %s failed: %s", symbol, type(exc).__name__)
        return None
    finally:
        if own:
            client.close()
