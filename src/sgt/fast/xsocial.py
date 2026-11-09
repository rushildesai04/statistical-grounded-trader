"""X (Twitter) at entry: attention + a targeted CATALYST check (owner 2026-11-09: "use it correctly and wisely with
optimal searches ... rather than blind, random searches").

Why X at all: the fade's tested edge is news vs no news (no-news fades +1.7%, news fades lose). Our news filter is
Benzinga; small-cap press releases, FDA/contract news and offerings often show up on X first or only. So X is used for
two narrow questions, only for trades the book actually takes (never for every alert):

1. Attention: ``/2/tweets/counts/recent`` for ``$SYM -is:retweet`` over the last hour (minute buckets) -> ``x_n60``,
   ``x_n15``. $0.005 (counts are billed per request).
2. Catalyst: counts of ``$SYM`` posts in the last 24 hours that ALSO carry a catalyst word (announces, press release,
   FDA, contract, merger, ...) -> ``x_cat24``, $0.005; and only when there are any, the <= 10 most RELEVANT of them
   (search, sort_order=relevancy) -> ``x_cat_posts`` (5 short texts for the Notes page and the AI review), <= $0.05.

Worst case $0.06 per trade; at most ``DAILY_CAP`` lookups a day; one lookup per symbol per ``CACHE_TTL``. Point in
time: every window ends at the entry minus ``END_LAG``. Recorded forward (prereg_fade_x_2026-11-08 amendment A2), not
a trading filter until proven. None = dormant (no key / disabled), capped or unknown; never "no posts".
"""
from __future__ import annotations

import html
import logging
import os
import re
from datetime import datetime, timedelta, timezone

import httpx

from sgt.core.secrets import get_secret

log = logging.getLogger(__name__)
COUNTS_URL = "https://api.x.com/2/tweets/counts/recent"
SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
TIMEOUT, MAX_BYTES, DAILY_CAP, SAMPLE, KEEP_POSTS, POST_CHARS = 6.0, 1024 * 1024, 60, 10, 5, 280
END_LAG = timedelta(seconds=15)                  # X refuses end_time < 10 s before now
CACHE_TTL = timedelta(minutes=10)
CATALYST_WORDS = ('announces', '"press release"', 'FDA', 'approval', 'contract', 'merger', 'acquisition', 'acquire',
                  'offering', 'earnings', 'partnership', 'agreement', 'patent', 'clinical', '"trial results"')
_SYMBOL = re.compile(r"^[A-Z]{1,6}$")
_CALLS: dict[str, int] = {}                      # UTC date -> paid lookups by this process (the listener is one process)
_CACHE: dict[str, tuple[datetime, dict]] = {}    # symbol -> (lookup time, result)


def enabled() -> bool:
    return os.environ.get("SGT_X_ENABLED", "1") == "1"


def catalyst_query(symbol: str) -> str:
    return f"${symbol} ({' OR '.join(CATALYST_WORDS)}) -is:retweet"


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


def total(payload: dict) -> int | None:
    """Total posts in a counts answer (any granularity); None when it is not a counts answer."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    return sum(b["tweet_count"] for b in data if isinstance(b, dict) and isinstance(b.get("tweet_count"), int)
               and b["tweet_count"] >= 0)


def posts(payload: dict, at: datetime) -> list[dict]:
    """The most relevant catalyst posts (at or before ``at``), short and control-free: untrusted text for display."""
    data = payload.get("data") if isinstance(payload, dict) else None
    out = []
    for p in data if isinstance(data, list) else []:
        if not isinstance(p, dict):
            continue
        t = _ts(p.get("created_at"))
        if t is None or t > at:
            continue
        text = re.sub(r"[\x00-\x1f\x7f]+", " ", html.unescape(str(p.get("text") or ""))).strip()[:POST_CHARS]
        m = p.get("public_metrics") if isinstance(p.get("public_metrics"), dict) else {}
        likes = m.get("like_count") if isinstance(m.get("like_count"), int) else 0
        if text:
            out.append({"at": t.isoformat(timespec="minutes"), "likes": likes, "text": text})
        if len(out) >= KEEP_POSTS:
            break
    return out


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


def _take_call(day: str, db) -> bool:
    """Count one paid lookup against DAILY_CAP. With an OpsStore the count is persisted and atomic (adversarial F-5: an
    in-memory count reset on every restart/deploy); without one (tests, one-off scripts) it is per process."""
    if db is not None:
        return db.take(f"x_calls:{day}", 1, DAILY_CAP)
    if _CALLS.get(day, 0) >= DAILY_CAP:
        return False
    _CALLS[day] = _CALLS.get(day, 0) + 1
    return True


def lookup(symbol: str, at: datetime, client: httpx.Client | None = None, db=None) -> dict | None:
    """Attention (last hour) and catalyst evidence (last 24 hours) for ``symbol`` at ``at``; None = dormant/unknown.
    ``x_cat24`` / ``x_cat_posts`` are None when the attention call worked but the catalyst calls did not."""
    if not enabled():
        return None
    hit = _CACHE.get(symbol or "")
    if hit and timedelta(0) <= at - hit[0] <= CACHE_TTL:
        return dict(hit[1])
    day = at.astimezone(timezone.utc).date().isoformat()
    if not _SYMBOL.match(symbol or ""):
        return None
    token = get_secret("x_bearer", required=False)
    if not token:
        return None
    if not _take_call(day, db):                         # counted before the call: a failed call may still cost
        return None
    own = client is None
    client = client or httpx.Client(headers={"Authorization": f"Bearer {token}", "User-Agent": "statistical-grounded-trader/1.0"},
                                    timeout=TIMEOUT, follow_redirects=False)
    end = (at - END_LAG).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        c = counts(_get(client, COUNTS_URL, {"query": f"${symbol} -is:retweet", "granularity": "minute",
                                             "start_time": (at - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                             "end_time": end}, symbol) or {}, at)
        if c is None:
            return None
        out = {**c, "x_cat24": None, "x_cat_posts": None}
        day_window = {"query": catalyst_query(symbol), "end_time": end,
                      "start_time": (at - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        cat = total(_get(client, COUNTS_URL, {**day_window, "granularity": "hour"}, symbol) or {})
        if cat is not None:
            out["x_cat24"] = cat
            out["x_cat_posts"] = []
            if cat > 0:                                 # nothing to read -> skip the paid search, $0
                page = _get(client, SEARCH_URL, {**day_window, "max_results": SAMPLE, "sort_order": "relevancy",
                                                 "tweet.fields": "created_at,public_metrics"}, symbol)
                out["x_cat_posts"] = posts(page, at) if page is not None else None
        for k in [k for k, (t, _) in _CACHE.items() if at - t > CACHE_TTL]:
            del _CACHE[k]                               # bounded: only the last 10 minutes are kept
        _CACHE[symbol] = (at, out)
        return dict(out)
    finally:
        if own:
            client.close()
