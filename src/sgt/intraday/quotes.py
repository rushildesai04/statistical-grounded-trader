"""Alpaca SIP NBBO quote client + spread sampler, and the ``spread_samples`` table.

The decision model (docs/DECISION_MODEL.md §10, F1) needs *measured* execution cost,
not the price-bucket estimate in ``sgt.brokers.costs``. This module fetches real NBBO
quotes for short windows, derives a time-weighted median relative spread per window,
and persists it so :class:`~sgt.brokers.measured_costs.MeasuredCostModel` can read a
point-in-time half-spread per symbol x time-of-day bucket.

All external bytes are hostile: every quote row is strictly validated; crossed
(ask < bid) and locked (ask == bid) quotes are counted and excluded; non-positive or
malformed rows are counted and skipped. Every HTTP call has a timeout; 429/5xx get
bounded exponential backoff; 401/403 raise immediately (the free SIP plan returns 403
for windows newer than 15 minutes, so the sampler never requests inside that window).
Pagination is bounded. Nothing is logged that could expose a secret.
"""
from __future__ import annotations

import logging
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from statistics import median
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import httpx

from sgt.config import get_settings
from sgt.core.secrets import get_secret
from sgt.db.models import Base
from sgt.intraday.calendar import SessionCalendar, default_calendar
from sgt.intraday.sources import DataSourceAuthError, DataSourceError

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

log = logging.getLogger(__name__)
_UTC = timezone.utc
_ET = ZoneInfo("America/New_York")

_QUOTES_PATH = "/v2/stocks/quotes"
_PAGE_LIMIT = 10000
_MAX_PAGES = 200
_MAX_RETRIES = 5
_BACKOFF_BASE_S = 0.5
_BACKOFF_CAP_S = 30.0
_RECENCY_LIMIT_MIN = 15  # free SIP plan rejects (403) windows newer than this
_WINDOW_SECONDS = 60
_DEFAULT_WINDOWS = 8

SOURCE_ALPACA_SIP = "alpaca_sip"


# --------------------------------------------------------------- time-of-day buckets
# Buckets are keyed off minute-of-day in ET (hour*60+minute). Centers are the window
# midpoints, used only for nearest-bucket fallback in the point-in-time lookup.
_BUCKET_CENTER: dict[str, int] = {
    "pre": 405,          # 04:00-09:30
    "opening": 600,      # 09:30-10:30
    "rth_early": 645,    # 10:30-11:00
    "midday": 750,       # 11:00-14:00
    "rth_late": 870,     # 14:00-15:00
    "power_hour": 930,   # 15:00-16:00
    "post": 1080,        # 16:00-20:00
}
TOD_BUCKETS: tuple[str, ...] = tuple(_BUCKET_CENTER)


def tod_bucket(minute_of_day: int) -> str:
    """Time-of-day bucket for an ET minute-of-day. Total over all integers."""
    if minute_of_day < 570:
        return "pre"
    if minute_of_day < 630:
        return "opening"
    if minute_of_day < 660:
        return "rth_early"
    if minute_of_day < 840:
        return "midday"
    if minute_of_day < 900:
        return "rth_late"
    if minute_of_day < 960:
        return "power_hour"
    return "post"


def bucket_center(bucket: str) -> int:
    return _BUCKET_CENTER[bucket]


# --------------------------------------------------------------------------- ORM
class SpreadSampleRow(Base):
    """One measured spread sample: symbol x session x window (minute-of-day).

    Primary key ``(symbol, session_date, minute_of_day)`` makes re-sampling a window
    an idempotent upsert. ``session_date`` is an ISO string so it sorts lexically and
    carries no spurious timezone. ``rel_spread_median`` is the time-weighted median of
    ``(ask - bid) / mid`` over the ~60s window (a full-spread fraction; the half-spread
    is half of it).
    """

    __tablename__ = "spread_samples"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    session_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    minute_of_day: Mapped[int] = mapped_column(Integer, primary_key=True)
    segment: Mapped[str] = mapped_column(String(8))
    rel_spread_median: Mapped[float] = mapped_column(Float)
    n_quotes: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), default=SOURCE_ALPACA_SIP)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


# ------------------------------------------------------------------------- quotes
@dataclass(frozen=True, slots=True)
class Quote:
    ts: datetime
    bid: float
    ask: float
    bid_size: float
    ask_size: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def rel_spread(self) -> float:
        return (self.ask - self.bid) / self.mid


@dataclass(frozen=True, slots=True)
class QuoteFetch:
    """Validated quotes plus counts of what was excluded (never silently dropped)."""

    quotes: list[Quote]
    crossed: int
    locked: int
    malformed: int


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


class AlpacaQuotes:
    """Alpaca Market Data v2 historical NBBO quotes (consolidated SIP or IEX)."""

    name = "alpaca_quotes"

    def __init__(
        self,
        feed: str | None = None,
        client: httpx.Client | None = None,
        *,
        sleep: Callable[[float], None] = _time.sleep,
        max_pages: int = _MAX_PAGES,
    ) -> None:
        settings = get_settings()
        self.feed = feed or settings.alpaca_feed
        self.base_url = settings.alpaca_data_url.rstrip("/")
        self.timeout = settings.http_timeout_s
        self._client = client or httpx.Client(timeout=self.timeout)
        self._owns_client = client is None
        self._sleep = sleep
        self._max_pages = max_pages

    def _get(self, params: dict[str, Any]) -> dict[str, Any]:
        headers = _alpaca_headers()
        url = self.base_url + _QUOTES_PATH
        for attempt in range(_MAX_RETRIES + 1):
            try:
                resp = self._client.get(url, params=params, headers=headers, timeout=self.timeout)
            except httpx.TransportError as exc:
                # transient network faults (disconnect, timeout, reset): bounded retry.
                if attempt < _MAX_RETRIES:
                    self._sleep(_backoff(attempt))
                    continue
                raise DataSourceError(f"alpaca quotes transport error after {attempt + 1} tries: "
                                      f"{type(exc).__name__}") from exc
            sc = resp.status_code
            if sc == 200:
                return resp.json()
            if sc in (401, 403):
                raise DataSourceAuthError(f"alpaca quotes auth failed: HTTP {sc}")
            if sc == 429 or 500 <= sc < 600:
                if attempt < _MAX_RETRIES:
                    self._sleep(_backoff(attempt))
                    continue
                raise DataSourceError(f"alpaca quotes unavailable after {attempt + 1} tries: HTTP {sc}")
            raise DataSourceError(f"alpaca quotes request failed: HTTP {sc} {resp.text[:200]!r}")
        raise DataSourceError("alpaca quotes retries exhausted")  # pragma: no cover

    def fetch_quotes(self, symbol: str, start: datetime, end: datetime) -> QuoteFetch:
        """NBBO quotes for ``symbol`` in ``[start, end)``, strictly validated.

        Crossed (ask < bid) and locked (ask == bid) quotes are excluded and counted;
        non-positive/malformed rows are counted and skipped. Pagination is bounded.
        """
        sym = symbol.upper()
        quotes: list[Quote] = []
        crossed = locked = malformed = 0
        page_token: str | None = None
        pages = 0
        while True:
            params: dict[str, Any] = {
                "symbols": sym,
                "start": _rfc3339(start),
                "end": _rfc3339(end),
                "feed": self.feed,
                "limit": _PAGE_LIMIT,
            }
            if page_token:
                params["page_token"] = page_token
            payload = self._get(params)
            quotes_by_sym = payload.get("quotes") or {}
            if not isinstance(quotes_by_sym, dict):
                raise DataSourceError("alpaca quotes response 'quotes' is not an object")
            rows = quotes_by_sym.get(sym)
            if isinstance(rows, list):
                for row in rows:
                    q = _validate_quote(row)
                    if q is None:
                        malformed += 1
                    elif q == "crossed":
                        crossed += 1
                    elif q == "locked":
                        locked += 1
                    else:
                        quotes.append(q)
            page_token = payload.get("next_page_token")
            pages += 1
            if not page_token:
                break
            if pages >= self._max_pages:
                raise DataSourceError(
                    f"alpaca quotes pagination exceeded {self._max_pages} pages; narrow the window"
                )
        quotes.sort(key=lambda q: q.ts)
        if crossed or locked or malformed:
            log.debug("quotes %s: excluded crossed=%d locked=%d malformed=%d",
                      sym, crossed, locked, malformed)
        return QuoteFetch(quotes=quotes, crossed=crossed, locked=locked, malformed=malformed)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def _validate_quote(row: Any) -> Quote | str | None:
    """Return a :class:`Quote`, or ``"crossed"``/``"locked"`` for excluded quotes,
    or ``None`` if the row is malformed. Raises nothing."""
    if not isinstance(row, Mapping):
        return None
    try:
        ts = _parse_ts(str(row["t"]))
        bid = float(row["bp"])
        ask = float(row["ap"])
        bid_size = float(row.get("bs", 0.0) or 0.0)
        ask_size = float(row.get("as", 0.0) or 0.0)
    except (KeyError, TypeError, ValueError):
        return None
    if bid <= 0.0 or ask <= 0.0:
        return None
    if ask < bid:
        return "crossed"
    if ask == bid:
        return "locked"
    return Quote(ts=ts, bid=bid, ask=ask, bid_size=bid_size, ask_size=ask_size)


# ----------------------------------------------------------------------- sampler
@dataclass(frozen=True, slots=True)
class SpreadSample:
    symbol: str
    session_date: date
    minute_of_day: int
    segment: str
    rel_spread_median: float
    n_quotes: int
    mid_price: float  # representative window price (report-only; not persisted)


def _weighted_median(pairs: list[tuple[float, float]]) -> float:
    """Weighted median of (value, weight) pairs. Falls back to the unweighted median
    when all weights are non-positive."""
    total = sum(w for _, w in pairs if w > 0)
    if total <= 0.0:
        return median(v for v, _ in pairs)
    acc = 0.0
    half = total / 2.0
    for v, w in sorted(pairs):
        if w <= 0:
            continue
        acc += w
        if acc >= half:
            return v
    return sorted(pairs)[-1][0]  # pragma: no cover


def window_spread(quotes: Sequence[Quote], window_end: datetime) -> tuple[float, float] | None:
    """Time-weighted median relative spread and median mid over a window.

    Each quote holds until the next quote (the last until ``window_end``); its dwell
    time is its weight. Returns ``None`` if there are no quotes.
    """
    if not quotes:
        return None
    qs = sorted(quotes, key=lambda q: q.ts)
    pairs: list[tuple[float, float]] = []
    for i, q in enumerate(qs):
        nxt = qs[i + 1].ts if i + 1 < len(qs) else window_end
        dwell = max(0.0, (nxt - q.ts).total_seconds())
        pairs.append((q.rel_spread, dwell))
    rel = _weighted_median(pairs)
    mid = median(q.mid for q in qs)
    return rel, mid


class SpreadSampler:
    """Samples K short windows across a session and derives per-window spreads."""

    def __init__(
        self,
        fetch: Callable[[str, datetime, datetime], QuoteFetch],
        *,
        calendar: SessionCalendar | None = None,
        sleep: Callable[[float], None] = _time.sleep,
        pacing_s: float = 0.35,
        now: Callable[[], datetime] | None = None,
        window_seconds: int = _WINDOW_SECONDS,
    ) -> None:
        self._fetch = fetch
        self._cal = calendar or default_calendar()
        self._sleep = sleep
        self._pacing_s = pacing_s
        self._now = now or (lambda: datetime.now(_UTC))
        self._window_seconds = window_seconds

    def _anchors(self, d: date) -> list[datetime]:
        """Window start times spread across the session's segments, oldest first."""
        b = self._cal.bounds(d)
        midday = datetime.combine(d, datetime.min.time(), tzinfo=_ET).replace(hour=12, minute=30)
        midday = midday.astimezone(_UTC)
        raw = [
            b.rth_open - timedelta(minutes=90),   # pre
            b.rth_open,                            # opening
            b.rth_open + timedelta(minutes=15),    # opening
            b.rth_open + timedelta(minutes=60),    # rth_early
            midday,                                # midday
            b.rth_close - timedelta(minutes=120),  # rth_late
            b.rth_close - timedelta(minutes=30),   # power_hour
            b.rth_close + timedelta(minutes=30),   # post
        ]
        # keep only anchors whose whole window stays inside the session envelope
        out = [a for a in raw if b.pre_open <= a and a + timedelta(seconds=self._window_seconds) <= b.post_close]
        return out

    def sample_session(self, symbol: str, d: date, *, windows: int = _DEFAULT_WINDOWS) -> list[SpreadSample]:
        sym = symbol.upper()
        if windows <= 0:
            raise ValueError("windows must be > 0")
        anchors = self._anchors(d)[:windows]
        cutoff = self._now() - timedelta(minutes=_RECENCY_LIMIT_MIN)
        out: list[SpreadSample] = []
        for a in anchors:
            end = a + timedelta(seconds=self._window_seconds)
            if end > cutoff:
                continue  # inside the 15-min recency wall: the API would 403
            fetched = self._fetch(sym, a, end)
            self._sleep(self._pacing_s)
            ws = window_spread(fetched.quotes, end)
            if ws is None:
                continue
            rel, mid = ws
            et = a.astimezone(_ET)
            seg = self._cal.segment(a)
            out.append(SpreadSample(
                symbol=sym,
                session_date=d,
                minute_of_day=et.hour * 60 + et.minute,
                segment=seg.value if seg is not None else "pre",
                rel_spread_median=rel,
                n_quotes=len(fetched.quotes),
                mid_price=mid,
            ))
        return out


def persist_samples(session, samples: Sequence[SpreadSample]) -> int:
    """Idempotent upsert of samples into ``spread_samples``. Returns rows written."""
    now = datetime.now(_UTC)
    written = 0
    for s in samples:
        session.merge(SpreadSampleRow(
            symbol=s.symbol,
            session_date=s.session_date.isoformat(),
            minute_of_day=s.minute_of_day,
            segment=s.segment,
            rel_spread_median=s.rel_spread_median,
            n_quotes=s.n_quotes,
            source=SOURCE_ALPACA_SIP,
            created_at=now,
        ))
        written += 1
    return written
