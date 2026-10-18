"""Historical and live 1-minute bar sources.

Implements :class:`~sgt.core.interfaces.HistoricalBarSource` (Alpaca, yfinance) and
:class:`~sgt.core.interfaces.LiveBarSource` (Alpaca delayed/real-time polling).

All external payloads are treated as hostile: every row is strictly validated and
malformed rows are skipped with a counted, logged warning (never silently). Every
HTTP call has a timeout; transient failures (429/5xx) get bounded exponential
backoff; auth failures (401/403) raise immediately. Bars are annotated through the
session calendar so ``session_date``/``minute_of_day``/``segment`` are authoritative.
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Callable, Mapping, Sequence

import httpx

from sgt.core.interfaces import HistoricalBarSource, LiveBarSource
from sgt.core.secrets import get_secret
from sgt.core.types import Bar
from sgt.config import get_settings
from sgt.intraday.calendar import SessionCalendar, default_calendar

log = logging.getLogger(__name__)
_UTC = timezone.utc

_BARS_PATH = "/v2/stocks/bars"
_TIMEFRAME = "1Min"
_PAGE_LIMIT = 10000
_MAX_PAGES = 1000
_MAX_RETRIES = 5
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 30.0


class DataSourceError(RuntimeError):
    """A bar source failed after exhausting retries, or returned an unusable response."""


class DataSourceAuthError(DataSourceError):
    """Authentication/authorization failure (HTTP 401/403). Never retried."""


def _rfc3339(dt: datetime) -> str:
    return dt.astimezone(_UTC).isoformat().replace("+00:00", "Z")


def _parse_ts(raw: str) -> datetime:
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.astimezone(_UTC)


def _alpaca_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": get_secret("alpaca_key_id") or "",
        "APCA-API-SECRET-KEY": get_secret("alpaca_secret_key") or "",
    }


def _backoff(attempt: int) -> float:
    return min(_BACKOFF_CAP_S, _BACKOFF_BASE_S * (2 ** attempt))


def _bar_from_alpaca(symbol: str, row: Mapping[str, Any], cal: SessionCalendar) -> Bar | None:
    """Validate one Alpaca bar dict and annotate it. Returns None if the row is
    malformed or falls outside any session window. Raises nothing."""
    try:
        ts = _parse_ts(str(row["t"]))
        o = float(row["o"]); h = float(row["h"]); low = float(row["l"]); c = float(row["c"])
        v = float(row["v"])
        vw = float(row["vw"]) if row.get("vw") is not None else None
        n = int(row["n"]) if row.get("n") is not None else None
    except (KeyError, TypeError, ValueError):
        return None
    try:
        return cal.annotate(symbol, ts, o, h, low, c, v, vwap=vw, trade_count=n)
    except ValueError:
        # OHLC consistency / price>0 violations from the vendor: reject, do not store.
        return None


class AlpacaHistoricalBars(HistoricalBarSource):
    """Alpaca Market Data v2 historical 1-minute bars (consolidated SIP or IEX)."""

    name = "alpaca"

    def __init__(
        self,
        feed: str | None = None,
        client: httpx.Client | None = None,
        *,
        calendar: SessionCalendar | None = None,
        sleep: Callable[[float], None] = _time.sleep,
        max_pages: int = _MAX_PAGES,
    ) -> None:
        settings = get_settings()
        self.feed = feed or settings.alpaca_feed
        self.base_url = settings.alpaca_data_url.rstrip("/")
        self.timeout = settings.http_timeout_s
        self._client = client or httpx.Client(timeout=self.timeout)
        self._owns_client = client is None
        self._cal = calendar or default_calendar()
        self._sleep = sleep
        self._max_pages = max_pages

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        headers = _alpaca_headers()
        url = self.base_url + _BARS_PATH
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._client.get(url, params=params, headers=headers, timeout=self.timeout)
            except httpx.TransportError as exc:
                # Dropped connections / timeouts are transient (2026-10-17: an unretried
                # RemoteProtocolError aborted a multi-hour backfill).
                if attempt < _MAX_RETRIES:
                    log.warning("alpaca transport error (%s); retrying", type(exc).__name__)
                    self._sleep(_backoff(attempt))
                    continue
                raise DataSourceError(f"alpaca unreachable after {attempt + 1} tries: {type(exc).__name__}") from exc
            sc = resp.status_code
            if sc == 200:
                return resp.json()
            if sc in (401, 403):
                raise DataSourceAuthError(f"alpaca auth failed: HTTP {sc}")
            if sc == 429 or 500 <= sc < 600:
                if attempt < _MAX_RETRIES:
                    self._sleep(_backoff(attempt))
                    continue
                raise DataSourceError(f"alpaca unavailable after {attempt + 1} tries: HTTP {sc}")
            raise DataSourceError(f"alpaca request failed: HTTP {sc} {resp.text[:200]!r}")
        raise DataSourceError("alpaca retries exhausted")  # pragma: no cover

    def fetch_many(
        self, symbols: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, list[Bar]]:
        syms = [s.upper() for s in symbols]
        if not syms:
            return {}
        out: dict[str, list[Bar]] = {s: [] for s in syms}
        skipped = 0
        page_token: str | None = None
        pages = 0
        while True:
            params: dict[str, Any] = {
                "symbols": ",".join(syms),
                "timeframe": _TIMEFRAME,
                "start": _rfc3339(start),
                "end": _rfc3339(end),
                "feed": self.feed,
                "adjustment": "raw",
                "limit": _PAGE_LIMIT,
            }
            if page_token:
                params["page_token"] = page_token
            payload = self._get(params)
            bars_by_sym = payload.get("bars") or {}
            if not isinstance(bars_by_sym, dict):
                raise DataSourceError("alpaca response 'bars' is not an object")
            for sym, rows in bars_by_sym.items():
                su = sym.upper()
                if su not in out or not isinstance(rows, list):
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        skipped += 1
                        continue
                    bar = _bar_from_alpaca(su, row, self._cal)
                    if bar is None:
                        skipped += 1
                        continue
                    out[su].append(bar)
            page_token = payload.get("next_page_token")
            pages += 1
            if not page_token:
                break
            if pages >= self._max_pages:
                raise DataSourceError(
                    f"alpaca pagination exceeded {self._max_pages} pages; narrow the range"
                )
        if skipped:
            log.warning("alpaca: skipped %d malformed/out-of-session bar rows", skipped)
        for s in out:
            out[s].sort(key=lambda b: b.ts)
        return out

    def fetch(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        return self.fetch_many([symbol], start, end).get(symbol.upper(), [])

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class YFinanceBars(HistoricalBarSource):
    """yfinance 1-minute bars (recent days only; ~7 days per call, ~30 days total)."""

    name = "yfinance"

    def __init__(
        self,
        *,
        calendar: SessionCalendar | None = None,
        prepost: bool = True,
        downloader: Callable[[str, datetime, datetime, bool], Any] | None = None,
    ) -> None:
        self._cal = calendar or default_calendar()
        self._prepost = prepost
        self._download = downloader or self._yf_download

    @staticmethod
    def _yf_download(symbol: str, start: datetime, end: datetime, prepost: bool):
        import yfinance as yf

        return yf.Ticker(symbol).history(
            start=start.astimezone(_UTC),
            end=end.astimezone(_UTC),
            interval="1m",
            prepost=prepost,
            auto_adjust=False,
            actions=False,
        )

    def fetch(self, symbol: str, start: datetime, end: datetime) -> list[Bar]:
        symbol = symbol.upper()
        df = self._download(symbol, start, end, self._prepost)
        if df is None or getattr(df, "empty", True):
            return []
        bars: list[Bar] = []
        dropped = 0
        for idx, row in df.iterrows():
            ts_py = idx.to_pydatetime()
            if ts_py.tzinfo is None:
                ts_py = ts_py.replace(tzinfo=_UTC)
            ts = ts_py.astimezone(_UTC)
            try:
                o = float(row["Open"]); h = float(row["High"])
                low = float(row["Low"]); c = float(row["Close"])
                v = float(row["Volume"])
            except (KeyError, TypeError, ValueError):
                dropped += 1
                continue
            vals = (o, h, low, c)
            if any(x != x for x in vals) or min(vals) <= 0:  # NaN or non-positive
                dropped += 1
                continue
            bar = self._cal.annotate(symbol, ts, o, h, low, c, max(0.0, v))
            if bar is None:
                dropped += 1
                continue
            bars.append(bar)
        if dropped:
            log.warning("yfinance %s: dropped %d zero/NaN/out-of-session rows", symbol, dropped)
        bars.sort(key=lambda b: b.ts)
        return bars


class AlpacaPollingLiveSource(LiveBarSource):
    """Polls Alpaca's multi-symbol bars endpoint once per minute and yields each
    COMPLETED 1-minute bar exactly once, in time order per symbol.

    A bar stamped at open ``t`` is complete only when ``t + 1min <= now - delay``.
    Missing minutes (no trades) are legitimate and counted as gaps, never fabricated.
    """

    name = "alpaca_live"

    def __init__(
        self,
        feed: str | None = None,
        *,
        delay_minutes: int = 0,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
        client: httpx.AsyncClient | None = None,
        calendar: SessionCalendar | None = None,
        poll_offset_s: float = 5.0,
    ) -> None:
        settings = get_settings()
        self.feed = feed or settings.alpaca_feed
        self.consolidated = self.feed in ("sip", "delayed_sip")
        self.delay_minutes = 15 if self.feed == "delayed_sip" else delay_minutes
        self.base_url = settings.alpaca_data_url.rstrip("/")
        self.timeout = settings.http_timeout_s
        self._clock = clock or (lambda: datetime.now(_UTC))
        self._sleep = sleep
        self._client = client or httpx.AsyncClient(timeout=self.timeout)
        self._owns_client = client is None
        self._cal = calendar or default_calendar()
        self._poll_offset_s = poll_offset_s
        self.gaps: dict[str, int] = {}

    async def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        headers = _alpaca_headers()
        url = self.base_url + _BARS_PATH
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = await self._client.get(url, params=params, headers=headers, timeout=self.timeout)
            except httpx.TransportError as exc:
                if attempt < _MAX_RETRIES:
                    log.warning("alpaca transport error (%s); retrying", type(exc).__name__)
                    await self._sleep(_backoff(attempt))
                    continue
                raise DataSourceError(f"alpaca unreachable after {attempt + 1} tries: {type(exc).__name__}") from exc
            sc = resp.status_code
            if sc == 200:
                return resp.json()
            if sc in (401, 403):
                raise DataSourceAuthError(f"alpaca auth failed: HTTP {sc}")
            if sc == 429 or 500 <= sc < 600:
                if attempt < _MAX_RETRIES:
                    await self._sleep(_backoff(attempt))
                    continue
                raise DataSourceError(f"alpaca unavailable after {attempt + 1} tries: HTTP {sc}")
            raise DataSourceError(f"alpaca request failed: HTTP {sc} {resp.text[:200]!r}")
        raise DataSourceError("alpaca retries exhausted")  # pragma: no cover

    def _count_gaps(self, symbol: str, prev_ts: datetime, ts: datetime) -> None:
        cursor = prev_ts + timedelta(minutes=1)
        while cursor < ts:
            if self._cal.segment(cursor) is not None:
                self.gaps[symbol] = self.gaps.get(symbol, 0) + 1
            cursor += timedelta(minutes=1)

    async def stream(self, symbols: Sequence[str]) -> AsyncIterator[Bar]:
        syms = [s.upper() for s in symbols]
        last_ts: dict[str, datetime] = {}
        fetch_start = None
        while True:
            now = self._clock().astimezone(_UTC)
            completed_until = (now - timedelta(minutes=self.delay_minutes)).replace(
                second=0, microsecond=0
            ) - timedelta(minutes=1)
            if fetch_start is None:
                fetch_start = completed_until - timedelta(minutes=2)
            params = {
                "symbols": ",".join(syms),
                "timeframe": _TIMEFRAME,
                "start": _rfc3339(fetch_start),
                "end": _rfc3339(now),
                "feed": self.feed,
                "adjustment": "raw",
                "limit": _PAGE_LIMIT,
            }
            try:
                payload = await self._get(params)
            except DataSourceAuthError:
                raise
            except DataSourceError as exc:
                log.warning("alpaca live poll failed, backing off: %s", exc)
                await self._sleep(_backoff(0))
                continue
            bars_by_sym = payload.get("bars") or {}
            for sym in syms:
                rows = bars_by_sym.get(sym) if isinstance(bars_by_sym, dict) else None
                if not isinstance(rows, list):
                    continue
                ready: list[Bar] = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    bar = _bar_from_alpaca(sym, row, self._cal)
                    if bar is None:
                        continue
                    if bar.ts > completed_until:
                        continue  # in-progress minute: never emit
                    if sym in last_ts and bar.ts <= last_ts[sym]:
                        continue  # already emitted
                    ready.append(bar)
                ready.sort(key=lambda b: b.ts)
                for bar in ready:
                    if sym in last_ts:
                        self._count_gaps(sym, last_ts[sym], bar.ts)
                    last_ts[sym] = bar.ts
                    yield bar
            # advance cursor past what we've consumed to bound the request window
            if last_ts:
                fetch_start = min(last_ts.values())
            # sleep until the next minute boundary + offset
            now2 = self._clock().astimezone(_UTC)
            secs_to_next = 60 - now2.second - now2.microsecond / 1e6
            await self._sleep(secs_to_next + self._poll_offset_s)

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()
