"""X (Twitter) attention at alert time (owner decision 2026-11-08: add the X API next to StockTwits).

Cost-minimised design (owner 2026-11-08: "as little money as possible, as much ROI as we can"). X pay-per-use bills
search per POST RETURNED ($0.005 each, so up to $0.50 for a 100-post page) but the counts endpoint per REQUEST
($0.005). So:

1. ``/2/tweets/counts/recent`` (minute buckets) gives the pre-registered primary, ``x_n60``, plus ``x_n15``, exactly
   and uncapped (a 100-post page saturated on busy names), for $0.005.
2. Only when there were posts: one search page of the newest ``SAMPLE`` (10, the API minimum) posts for the
   secondary measures ``x_authors`` / ``x_eng`` (unique accounts and likes/reposts among those newest posts), <= $0.05.
3. One lookup per symbol per ``CACHE_TTL``: a second text for the same trade reuses the first answer.

Worst case per lookup $0.055 (was $0.50); at most ``DAILY_CAP`` paid lookups a day. Point in time: ``end_time`` = the
alert minus ``END_LAG``. Dormant (returns None) until the bearer token secret ``x_bearer`` exists; None also means
unknown (error, cap reached), never "no posts".
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from sgt.core.secrets import get_secret

log = logging.getLogger(__name__)
COUNTS_URL = "https://api.x.com/2/tweets/counts/recent"
SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
TIMEOUT, MAX_BYTES, DAILY_CAP, SAMPLE = 6.0, 1024 * 1024, 60, 10
END_LAG = timedelta(seconds=15)                  # X refuses end_time < 10 s before now
CACHE_TTL = timedelta(minutes=10)
_SYMBOL = re.compile(r"^[A-Z]{1,6}$")
_CALLS: dict[str, int] = {}                      # UTC date -> paid lookups by this process (the listener is one process)
_CACHE: dict[str, tuple[datetime, dict]] = {}    # symbol -> (lookup time, result)


def _ts(s) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def counts(payload: dict, at: datetime) -> dict | None:
    """``x_n60`` / ``x_n15`` from a minute-bucket counts payload; None when the payload is not a counts answer.
    A bucket counts toward the last 15 minutes when it ends after ``at`` - 15 min (at most one minute extra)."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    n60 = n15 = 0
    for b in data:
        if not isinstance(b, dict) or not isinstance(b.get("tweet_count"), int) or b["tweet_count"] < 0:
            continue
        end = _ts(b.get("end"))
        if end is None or end > at + timedelta(minutes=1):
            continue
        n60 += b["tweet_count"]
        if end > at - timedelta(minutes=15):
            n15 += b["tweet_count"]
    return {"x_n60": n60, "x_n15": n15}


def sample(payload: dict, at: datetime) -> dict:
    """Unique accounts and likes/reposts among the newest posts of a search page (posts at or before ``at``)."""
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
        rows.append((str(p.get("author_id") or ""), eng))
    return {"x_k": len(rows), "x_authors": len({a for a, _ in rows if a}), "x_eng": sum(e for _, e in rows)}


def _get(client: httpx.Client, url: str, params: dict, symbol: str) -> dict | None:
    try:
        r = client.get(url, params=params)
        if r.status_code != 200 or len(r.content) > MAX_BYTES:
            log.warning("x %s %s -> %s", url.rsplit("/", 2)[-2], symbol, r.status_code)  # never log the token
            return None
        j = r.json()
        return j if isinstance(j, dict) else None
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("x %s %s failed: %s", url.rsplit("/", 2)[-2], symbol, type(exc).__name__)
        return None


def lookup(symbol: str, at: datetime, client: httpx.Client | None = None) -> dict | None:
    """X attention for ``symbol`` in the hour before ``at``; None = dormant, capped or unknown.
    ``x_authors`` / ``x_eng`` are None when the counts call worked but the sample call did not."""
    if os.environ.get("SGT_X_ENABLED", "0") != "1":    # owner 2026-11-09: X spending off; budget goes to OpenAI
        return None
    hit = _CACHE.get(symbol or "")
    if hit and timedelta(0) <= at - hit[0] <= CACHE_TTL:
        return dict(hit[1])
    day = at.astimezone(timezone.utc).date().isoformat()
    if not _SYMBOL.match(symbol or "") or _CALLS.get(day, 0) >= DAILY_CAP:
        return None
    token = get_secret("x_bearer", required=False)
    if not token:
        return None
    _CALLS[day] = _CALLS.get(day, 0) + 1                # counted before the call: a failed call may still cost
    own = client is None
    client = client or httpx.Client(headers={"Authorization": f"Bearer {token}", "User-Agent": "statistical-grounded-trader/1.0"},
                                    timeout=TIMEOUT, follow_redirects=False)
    window = {"query": f"${symbol} -is:retweet",
              "start_time": (at - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "end_time": (at - END_LAG).strftime("%Y-%m-%dT%H:%M:%SZ")}
    try:
        c = counts(_get(client, COUNTS_URL, {**window, "granularity": "minute"}, symbol) or {}, at)
        if c is None:
            return None
        out = {**c, "x_k": 0, "x_authors": 0, "x_eng": 0}
        if c["x_n60"] > 0:                              # nothing posted -> the sample would be empty: skip, $0
            page = _get(client, SEARCH_URL, {**window, "max_results": SAMPLE,
                                             "tweet.fields": "created_at,author_id,public_metrics"}, symbol)
            out.update(sample(page, at) if page is not None else {"x_k": None, "x_authors": None, "x_eng": None})
        for k in [k for k, (t, _) in _CACHE.items() if at - t > CACHE_TTL]:
            del _CACHE[k]                               # bounded: only the last 10 minutes are kept
        _CACHE[symbol] = (at, out)
        return dict(out)
    finally:
        if own:
            client.close()
