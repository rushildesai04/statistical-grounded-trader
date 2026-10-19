"""Live in-play scanner: today's gap-ups at the open (C forward test, 2026-10-18).

Same rule the H2 datasets were built on (sgt.scanner.gappers): today's official open >=
``min_gap`` over the previous close, open inside the price band, previous-day volume >=
``min_prev_volume`` -- all known at the opening print. Alpaca snapshots give today's daily
bar (open) and the previous daily bar (close, volume) for many symbols per request.

A daemon thread fetches once per session shortly after 09:30 ET and caches the set;
:meth:`symbols` only reads the cache, so the trading event loop never blocks on HTTP.
Errors are logged and retried; on failure the cache stays empty (fail closed: no
unscreened symbols are added).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

import httpx

from sgt.config import get_settings
from sgt.intraday.calendar import ET, default_calendar
from sgt.intraday.sources import _alpaca_headers

log = logging.getLogger(__name__)
_BATCH = 400


def select_gappers(snapshots: Mapping[str, Any], *, min_gap: float = 0.10,
                   price_min: float = 1.0, price_max: float = 30.0,
                   min_prev_volume: float = 50_000, today: date | None = None) -> set[str]:
    """Pure filter over Alpaca snapshot payloads ``{symbol: {dailyBar, prevDailyBar}}``."""
    out: set[str] = set()
    for sym, snap in snapshots.items():
        if not isinstance(snap, dict):
            continue
        day, prev = snap.get("dailyBar"), snap.get("prevDailyBar")
        if not isinstance(day, dict) or not isinstance(prev, dict):
            continue
        try:
            o, pc, pv = float(day["o"]), float(prev["c"]), float(prev["v"])
            day_date = date.fromisoformat(str(day["t"])[:10])
        except (KeyError, TypeError, ValueError):
            continue
        if today is not None and day_date != today:
            continue                      # stale snapshot (no print yet today)
        if not all(math.isfinite(x) for x in (o, pc, pv)) or pc <= 0:
            continue
        if price_min <= o <= price_max and pv >= min_prev_volume and o / pc - 1.0 >= min_gap:
            out.add(str(sym).upper())
    return out


class LiveGapScanner:
    def __init__(self, symbols: Iterable[str], *, delay_after_open_s: float = 70.0,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 fetch: Callable[[list[str]], Mapping[str, Any]] | None = None,
                 **rule: float) -> None:
        self._universe = sorted({s.upper() for s in symbols})
        self._delay = delay_after_open_s
        self._clock = clock
        self._fetch = fetch or self._fetch_snapshots
        self._rule = rule
        self._lock = threading.Lock()
        self._session: date | None = None
        self._cache: frozenset[str] = frozenset()
        self._thread: threading.Thread | None = None

    def symbols(self) -> frozenset[str]:
        with self._lock:
            today = self._clock().astimezone(ET).date()
            return self._cache if self._session == today else frozenset()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="gap-scanner", daemon=True)
        self._thread.start()

    def scan_now(self, today: date) -> frozenset[str]:
        found: set[str] = set()
        for i in range(0, len(self._universe), _BATCH):
            snaps = self._fetch(self._universe[i:i + _BATCH])
            found |= select_gappers(snaps, today=today, **self._rule)
        with self._lock:
            self._session, self._cache = today, frozenset(found)
        log.info("gap scanner %s: %d in-play symbols", today, len(found))
        return self._cache

    def _loop(self) -> None:
        cal = default_calendar()
        while True:
            now = self._clock()
            today = now.astimezone(ET).date()
            ready_at = None
            if cal.is_session(today):
                ready_at = cal.bounds(today).rth_open + timedelta(seconds=self._delay)
            if ready_at is not None and now >= ready_at and self._session != today:
                for attempt in range(3):
                    try:
                        self.scan_now(today)
                        break
                    except Exception as exc:  # noqa: BLE001 - logged; cache stays empty
                        log.warning("gap scan failed (attempt %d): %s", attempt + 1, exc)
                        time.sleep(5 * (attempt + 1))
            time.sleep(15)

    @staticmethod
    def _fetch_snapshots(symbols: list[str]) -> Mapping[str, Any]:
        s = get_settings()
        resp = httpx.get(s.alpaca_data_url.rstrip("/") + "/v2/stocks/snapshots",
                         params={"symbols": ",".join(symbols), "feed": s.alpaca_feed},
                         headers=_alpaca_headers(), timeout=s.http_timeout_s)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}
