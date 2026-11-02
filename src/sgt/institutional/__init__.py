"""Point-in-time institutional-flow datasets (FINRA short volume, SEC 13F, CUSIP map).

Ingest/validate/store only: nothing here touches prices or returns. Shared helpers for
the three sources live here: polite retrying downloads with byte caps, a request-rate
limiter, atomic parquet writes, and XNYS session navigation for ``available_date``.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from sgt.core.http import BoundedResponse, ResponseTooLarge, get_bounded
from sgt.intraday.calendar import default_calendar

log = logging.getLogger(__name__)

RETRIES = 4
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_SESSION_GAP = 10


class FetchError(RuntimeError):
    """A download failed after retries (transport errors or persistent 5xx/429)."""


class RateLimiter:
    """Thread-safe minimum interval between requests (e.g. SEC's 5 req/s cap)."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            time.sleep(delay)


def _backoff(attempt: int, resp_headers: httpx.Headers | None = None) -> float:
    if resp_headers is not None:
        ra = resp_headers.get("retry-after", "")
        if ra.isdigit():
            return min(float(ra), 120.0)
    return min(2.0 ** attempt, 60.0)


def get_with_retry(client: httpx.Client, url: str, *, max_bytes: int,
                   absent: frozenset[int] = frozenset({404}),
                   limiter: RateLimiter | None = None) -> BoundedResponse | None:
    """Bounded GET with retry/backoff. Returns None when the status is in ``absent``."""
    for attempt in range(RETRIES + 1):
        if limiter is not None:
            limiter.wait()
        try:
            resp = get_bounded(client, url, max_bytes=max_bytes)
        except httpx.TransportError as exc:
            if attempt == RETRIES:
                raise FetchError(f"{url}: {type(exc).__name__}") from exc
            time.sleep(_backoff(attempt))
            continue
        if resp.status_code in absent:
            return None
        if resp.status_code in _RETRY_STATUS:
            if attempt == RETRIES:
                raise FetchError(f"{url}: HTTP {resp.status_code} after {RETRIES} retries")
            time.sleep(_backoff(attempt, resp.headers))
            continue
        resp.raise_for_status(url)
        return resp
    raise FetchError(f"{url}: retries exhausted")


def download_to_file(client: httpx.Client, url: str, dest: Path, *, max_bytes: int,
                     limiter: RateLimiter | None = None) -> str:
    """Stream ``url`` to ``dest`` atomically (tmp + rename) under a byte cap; return sha256."""
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(RETRIES + 1):
        if limiter is not None:
            limiter.wait()
        h = hashlib.sha256()
        try:
            with client.stream("GET", url) as resp:
                if resp.status_code in _RETRY_STATUS:
                    status, headers = resp.status_code, resp.headers
                else:
                    if not 200 <= resp.status_code < 300:
                        raise FetchError(f"{url}: HTTP {resp.status_code}")
                    total = 0
                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise ResponseTooLarge(url, max_bytes)
                            h.update(chunk)
                            fh.write(chunk)
                    os.replace(tmp, dest)
                    return h.hexdigest()
        except httpx.TransportError as exc:
            tmp.unlink(missing_ok=True)
            if attempt == RETRIES:
                raise FetchError(f"{url}: {type(exc).__name__}") from exc
            time.sleep(_backoff(attempt))
            continue
        except ResponseTooLarge:
            tmp.unlink(missing_ok=True)
            raise
        if attempt == RETRIES:
            raise FetchError(f"{url}: HTTP {status} after {RETRIES} retries")
        time.sleep(_backoff(attempt, headers))
    raise FetchError(f"{url}: retries exhausted")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def write_parquet_atomic(table: pa.Table, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp)
    os.replace(tmp, path)


def sessions(start: date, end: date) -> list[date]:
    """XNYS sessions in ``[start, end]``."""
    cal = default_calendar()
    out, d = [], start
    while d <= end:
        if cal.is_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def next_session(d: date) -> date:
    """First XNYS session strictly after ``d``."""
    cal = default_calendar()
    nxt = d + timedelta(days=1)
    for _ in range(_MAX_SESSION_GAP):
        if cal.is_session(nxt):
            return nxt
        nxt += timedelta(days=1)
    raise ValueError(f"no XNYS session within {_MAX_SESSION_GAP} days after {d}")
