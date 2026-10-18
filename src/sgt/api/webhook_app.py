"""Public TradingView alert-webhook receiver (standalone ASGI app).

TradingView cannot sign requests, so authentication is a shared secret embedded in
the JSON body and compared in constant time against
``get_secret("tradingview_webhook_secret")``. This app is DELIBERATELY SEPARATE from
the control-plane and public signals apps and must never be mounted on them: it is
the one surface intended to be reachable from the internet (via a TLS tunnel), and
it exposes exactly one route, ``POST /tradingview/alert`` (plus ``/health``).

Security posture (fail closed):
- Per-source-IP in-memory token bucket -> 429 before any parsing work.
- Raw body size cap enforced BEFORE JSON parsing -> 413.
- Strict pydantic body (``extra="forbid"``, typed, bounded) -> 422 on anything off-shape.
- Secret unconfigured -> 503 (never open); wrong secret -> 401. The secret is compared
  with ``hmac.compare_digest`` on bytes and is NEVER logged or stored.
- Optional allowlist of source IPs/CIDRs -> 403 (default disabled; see runbook [VERIFY]).
- Storage is idempotent on (alert_name, ticker, bar time) and does only a single
  bounded DB write, so the response returns well within TradingView's ~3 s timeout.

Served only by ``scripts/run_webhook_receiver.py`` (loopback by default).
"""
from __future__ import annotations

import hmac
import ipaddress
import logging
import math
import threading
import time as _time
from datetime import datetime, timezone
from typing import Sequence

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from sgt.core.secrets import get_secret
from sgt.crowd.tradingview import MAX_INDICATORS, store_alert
from sgt.db.session import get_session

log = logging.getLogger(__name__)

WEBHOOK_SECRET_NAME = "tradingview_webhook_secret"
DEFAULT_MAX_BODY_BYTES = 8192
# Sane epoch bounds for the {{time}} placeholder (2000-01-01 .. 2100-01-01, UTC).
_MIN_EPOCH_S = 946_684_800.0
_MAX_EPOCH_S = 4_102_444_800.0
_TICKER_MAX = 16
_ALERT_NAME_MAX = 128
_INDICATOR_KEY_MAX = 64


# --------------------------------------------------------------------------- request model


class AlertBody(BaseModel):
    """Strict schema for a TradingView alert JSON body (``extra="forbid"``)."""

    model_config = ConfigDict(extra="forbid")

    secret: str = Field(min_length=1, max_length=256)
    ticker: str = Field(min_length=1, max_length=_TICKER_MAX)
    exchange: str | None = Field(default=None, max_length=32)
    time: datetime
    interval: str | None = Field(default=None, max_length=16)
    close: float | None = Field(default=None, gt=0.0, le=1_000_000.0)
    volume: float | None = Field(default=None, ge=0.0, le=1e15)
    alert_name: str = Field(min_length=1, max_length=_ALERT_NAME_MAX)
    indicators: dict[str, float] | None = None

    @field_validator("time", mode="before")
    @classmethod
    def _parse_time(cls, v: object) -> datetime:
        """Accept ISO-8601 (with optional trailing Z) or epoch milliseconds."""
        if isinstance(v, bool):
            raise ValueError("time must be an ISO string or epoch ms, not a bool")
        if isinstance(v, (int, float)):
            if isinstance(v, float) and not math.isfinite(v):
                raise ValueError("time must be finite")
            secs = float(v) / 1000.0
            if not (_MIN_EPOCH_S <= secs <= _MAX_EPOCH_S):
                raise ValueError("time (epoch ms) out of range")
            return datetime.fromtimestamp(secs, tz=timezone.utc)
        if isinstance(v, str):
            s = v.strip()
            if not s or len(s) > 40:
                raise ValueError("time string empty or too long")
            if s.isdigit():  # epoch ms as a string
                secs = int(s) / 1000.0
                if not (_MIN_EPOCH_S <= secs <= _MAX_EPOCH_S):
                    raise ValueError("time (epoch ms) out of range")
                return datetime.fromtimestamp(secs, tz=timezone.utc)
            if s.endswith(("Z", "z")):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        raise ValueError("time must be an ISO string or epoch ms")

    @field_validator("ticker")
    @classmethod
    def _norm_ticker(cls, v: str) -> str:
        v = v.strip().upper()
        if not v or any(c not in _TICKER_CHARS for c in v):
            raise ValueError("ticker must match [A-Z0-9.-]{1,16}")
        return v

    @field_validator("close", "volume")
    @classmethod
    def _finite(cls, v: float | None) -> float | None:
        if v is not None and not math.isfinite(v):
            raise ValueError("value must be finite")
        return v

    @field_validator("indicators")
    @classmethod
    def _check_indicators(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return None
        if len(v) > MAX_INDICATORS:
            raise ValueError(f"at most {MAX_INDICATORS} indicator entries allowed")
        for k, val in v.items():
            if not k or len(k) > _INDICATOR_KEY_MAX:
                raise ValueError("indicator name empty or too long")
            if not math.isfinite(val):
                raise ValueError(f"indicator {k!r} must be finite")
        return v


_TICKER_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")


# --------------------------------------------------------------------------- rate limiting


class _TokenBucket:
    """Per-key in-memory token bucket (monotonic clock, bounded key set)."""

    def __init__(self, capacity: float, refill_per_s: float, *, max_keys: int = 10_000) -> None:
        if capacity <= 0 or refill_per_s <= 0:
            raise ValueError("capacity and refill_per_s must be positive")
        self._capacity = float(capacity)
        self._refill = float(refill_per_s)
        self._max_keys = max_keys
        self._state: dict[str, tuple[float, float]] = {}  # key -> (tokens, last_ts)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = _time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (self._capacity, now))
            tokens = min(self._capacity, tokens + (now - last) * self._refill)
            if tokens < 1.0:
                self._state[key] = (tokens, now)
                return False
            tokens -= 1.0
            if key not in self._state and len(self._state) >= self._max_keys:
                # Bound memory: drop the stalest entry before inserting a new key.
                oldest = min(self._state, key=lambda k: self._state[k][1])
                del self._state[oldest]
            self._state[key] = (tokens, now)
            return True


# --------------------------------------------------------------------------- app factory


def create_app(
    *,
    rate_capacity: float = 60.0,
    rate_refill_per_s: float = 1.0,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    allowlist: Sequence[str] = (),
    trust_forwarded_header: str | None = None,
) -> FastAPI:
    """Build an isolated receiver app (own rate-limit state and config).

    ``allowlist`` is a list of IPs/CIDRs; empty disables the allowlist. When
    ``trust_forwarded_header`` is set (e.g. ``"x-forwarded-for"``) the client IP is
    taken from that header's first entry — enable ONLY behind a trusted proxy/tunnel
    that overwrites it, otherwise a client can spoof its IP.
    """
    if max_body_bytes <= 0:
        raise ValueError("max_body_bytes must be positive")
    networks = tuple(ipaddress.ip_network(c, strict=False) for c in allowlist)
    header = trust_forwarded_header.lower() if trust_forwarded_header else None
    bucket = _TokenBucket(rate_capacity, rate_refill_per_s)

    app = FastAPI(
        title="sgt-tradingview-webhook",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def _client_ip(request: Request) -> str:
        if header is not None:
            fwd = request.headers.get(header)
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _ip_allowed(ip: str) -> bool:
        if not networks:
            return True
        try:
            addr = ipaddress.ip_address(ip)
        except ValueError:
            return False
        return any(addr in net for net in networks)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/tradingview/alert")
    async def alert(request: Request) -> JSONResponse:
        ip = _client_ip(request)

        if not bucket.allow(ip):
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429)

        if not _ip_allowed(ip):
            log.warning("tradingview webhook rejected IP not in allowlist: %s", ip)
            return JSONResponse({"detail": "source not allowed"}, status_code=403)

        # Size cap BEFORE parsing. Trust Content-Length only as a fast reject; the
        # real bound is the actual body length (reading the stream is bounded by the
        # ASGI server, and we re-check here).
        raw = await request.body()
        if len(raw) > max_body_bytes:
            return JSONResponse({"detail": "payload too large"}, status_code=413)

        try:
            body = AlertBody.model_validate_json(raw)
        except ValidationError:
            # Do not echo the body: it carries the secret.
            return JSONResponse({"detail": "invalid alert body"}, status_code=422)

        configured = get_secret(WEBHOOK_SECRET_NAME, required=False)
        if not configured:
            return JSONResponse({"detail": "webhook secret not configured"}, status_code=503)
        if not hmac.compare_digest(body.secret.encode("utf-8"), configured.encode("utf-8")):
            log.warning("tradingview webhook auth failure from %s", ip)
            return JSONResponse({"detail": "invalid secret"}, status_code=401)

        received_at = datetime.now(timezone.utc)
        with get_session() as session:
            created = store_alert(
                session,
                alert_name=body.alert_name,
                ticker=body.ticker,
                ts=body.time,
                exchange=body.exchange,
                interval=body.interval,
                close=body.close,
                volume=body.volume,
                indicators=body.indicators,
                received_at=received_at,
            )
        return JSONResponse(
            {"status": "stored" if created else "duplicate", "ticker": body.ticker},
            status_code=200,
        )

    return app


# Default app served by scripts/run_webhook_receiver.py. Secrets/config beyond
# defaults are supplied by that script via create_app() when needed.
app = create_app()
