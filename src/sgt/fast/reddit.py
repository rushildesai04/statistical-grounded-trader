"""Reddit attention at alert time (owner decision 2026-11-08: add Reddit next to StockTwits and X).

Reddit's own Data API needs per-app approval and reportedly stopped taking new requests on 2026-10-31, so this reads
ApeWisdom's free public API (apewisdom.io/api), which counts ticker mentions across Reddit's stock subreddits
(filter ``all-stocks``): rank, mentions and upvotes over the last 24 hours, plus mentions 24 hours earlier. Coarser
than StockTwits / X (a day, not an hour), but it answers "is this name being talked about on Reddit right now".

Cost: free, no key. Fair use: the whole table (about 7 pages of 100) is fetched at most once per ``CACHE_TTL`` and
only when an alert needs it; generic User-Agent, timeout and size bound on every request, page count bounded.
Point in time: the table is the live state at the alert (never backfilled).

Result: ``rd_rank`` None + ``rd_mentions`` 0 = not in the table (not being discussed); ``None`` = unknown (API down,
malformed), never "not discussed".
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

import httpx

log = logging.getLogger(__name__)
URL = "https://apewisdom.io/api/v1.0/filter/all-stocks/page/{page}"
UA = "statistical-grounded-trader/1.0"
TIMEOUT, MAX_BYTES, MAX_PAGES = 5.0, 512 * 1024, 15
CACHE_TTL = timedelta(minutes=10)
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_TABLE: dict = {"at": None, "rows": None}        # last full table: {"at": datetime, "rows": {TICKER: row}}


def _int(v) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and re.fullmatch(r"-?\d{1,9}", v.strip()):
        return int(v)
    return None


def parse(payload: dict) -> tuple[dict[str, dict], int] | None:
    """Rows keyed by ticker + the page count from one page; None when the page is not an ApeWisdom answer."""
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return None
    pages = _int(payload.get("pages"))
    rows = {}
    for r in payload["results"]:
        if not isinstance(r, dict):
            continue
        t = str(r.get("ticker") or "").upper()
        rank, mentions = _int(r.get("rank")), _int(r.get("mentions"))
        if not _SYMBOL.match(t) or rank is None or mentions is None or rank < 1 or mentions < 0:
            continue
        rows[t] = {"rd_rank": rank, "rd_mentions": mentions, "rd_upvotes": _int(r.get("upvotes")),
                   "rd_mentions_prev": _int(r.get("mentions_24h_ago"))}
    return rows, max(1, min(pages or 1, MAX_PAGES))


def _page(client: httpx.Client, page: int) -> dict | None:
    try:
        r = client.get(URL.format(page=page))
        if r.status_code != 200 or len(r.content) > MAX_BYTES:
            log.warning("apewisdom page %d -> %s", page, r.status_code)
            return None
        return r.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("apewisdom page %d failed: %s", page, type(exc).__name__)
        return None


def table(at: datetime, client: httpx.Client | None = None) -> dict[str, dict] | None:
    """The full ranked table, fetched at most once per CACHE_TTL; None if any page fails (a partial table would
    report real mentions as 'not discussed')."""
    if _TABLE["rows"] is not None and _TABLE["at"] is not None and timedelta(0) <= at - _TABLE["at"] <= CACHE_TTL:
        return _TABLE["rows"]
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": UA}, timeout=TIMEOUT, follow_redirects=False)
    try:
        first = parse(_page(client, 1) or {})
        if first is None:
            return None
        rows, pages = first
        for p in range(2, pages + 1):
            nxt = parse(_page(client, p) or {})
            if nxt is None:
                return None
            for t, row in nxt[0].items():
                rows.setdefault(t, row)                 # a ticker shifting pages mid-fetch keeps its better rank
        _TABLE.update(at=at, rows=rows)
        return rows
    finally:
        if own:
            client.close()


def lookup(symbol: str, at: datetime, client: httpx.Client | None = None) -> dict | None:
    """Reddit attention for ``symbol`` at ``at``; None = unknown."""
    if not _SYMBOL.match(symbol or ""):
        return None
    rows = table(at, client)
    if rows is None:
        return None
    return dict(rows.get(symbol) or {"rd_rank": None, "rd_mentions": 0, "rd_upvotes": None, "rd_mentions_prev": None})
