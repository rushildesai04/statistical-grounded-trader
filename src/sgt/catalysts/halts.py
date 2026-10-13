"""Nasdaq Trader trading-halt feed.

Parses the public RSS feed (https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts)
into core :class:`HaltEvent` rows. Each ``<item>`` carries namespaced ``ndaq:*``
fields; we match on local names so an unexpected namespace URI does not break
parsing. Payloads are size-bounded and DTDs are rejected (defense against entity
expansion). Times are published in US/Eastern and converted to UTC.

The field names below follow the published feed and should be re-verified against a
live sample before production (see tests/catalysts/fixtures/nasdaq_halts.xml).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from xml.etree import ElementTree as ET
from zoneinfo import ZoneInfo

import httpx

from sgt.catalysts.models import HaltEventRow
from sgt.config import get_settings
from sgt.core.http import get_bounded
from sgt.core.types import HaltEvent, HaltState
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert

log = logging.getLogger(__name__)

ET_TZ = ZoneInfo("America/New_York")
_UTC = timezone.utc
HALTS_URL = "https://www.nasdaqtrader.com/rss.aspx?feed=tradehalts"
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024
SOURCE = "nasdaq_trader"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _combine_et(day: str, clock: str) -> datetime | None:
    """Combine MM/DD/YYYY + HH:MM:SS (ET) into a UTC datetime, or None if unparseable."""
    day = (day or "").strip()
    clock = (clock or "").strip()
    if not day or not clock:
        return None
    try:
        naive = datetime.strptime(f"{day} {clock}", "%m/%d/%Y %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=ET_TZ).astimezone(_UTC)


def parse_halts(payload: bytes) -> list[HaltEvent]:
    """Parse the halt RSS payload into HaltEvents. Raises ValueError on an oversized
    payload or a payload containing a DTD."""
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(f"halt feed payload too large: {len(payload)} bytes")
    if b"<!DOCTYPE" in payload or b"<!ENTITY" in payload:
        raise ValueError("halt feed payload contains a DTD/entity declaration; rejected")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError(f"halt feed is not well-formed XML: {exc}") from exc

    events: list[HaltEvent] = []
    for item in (el for el in root.iter() if _local(el.tag) == "item"):
        fields = {_local(c.tag): (c.text or "").strip() for c in item}
        symbol = fields.get("IssueSymbol", "").strip().upper()
        if not symbol:
            continue
        reason = fields.get("ReasonCode", "").strip()
        halt_ts = _combine_et(fields.get("HaltDate", ""), fields.get("HaltTime", ""))
        if halt_ts is not None:
            events.append(
                HaltEvent(symbol=symbol, ts=halt_ts, state=HaltState.HALTED,
                          reason_code=reason, source=SOURCE)
            )
        resume_ts = _combine_et(
            fields.get("ResumptionDate", ""), fields.get("ResumptionTradeTime", "")
        )
        if resume_ts is not None:
            events.append(
                HaltEvent(symbol=symbol, ts=resume_ts, state=HaltState.RESUMED,
                          reason_code=reason, source=SOURCE)
            )
    events.sort(key=lambda e: (e.symbol, e.ts, e.state.value))
    return events


class HaltsCollector:
    name = "halts"

    def __init__(
        self,
        url: str = HALTS_URL,
        client: httpx.Client | None = None,
        *,
        user_agent: str | None = None,
    ) -> None:
        settings = get_settings()
        self.url = url
        self.timeout = settings.http_timeout_s
        self.user_agent = user_agent or settings.sec_user_agent
        self._client = client or httpx.Client(timeout=self.timeout)
        self._owns_client = client is None

    def fetch(self) -> bytes:
        resp = get_bounded(self._client, self.url, max_bytes=MAX_PAYLOAD_BYTES,
                           headers={"User-Agent": self.user_agent}, timeout=self.timeout)
        resp.raise_for_status(self.url)
        return resp.content

    def collect(self) -> int:
        """Fetch, parse and idempotently upsert halt events. Returns rows upserted."""
        events = parse_halts(self.fetch())
        with get_session() as session:
            for e in events:
                _upsert(
                    session,
                    HaltEventRow,
                    {
                        "symbol": e.symbol,
                        "ts": e.ts,
                        "state": e.state.value,
                        "reason_code": e.reason_code,
                        "source": e.source,
                    },
                    ["symbol", "ts", "state"],
                )
        return len(events)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def load_halts(symbol: str, start: datetime | date, end: datetime | date) -> list[HaltEvent]:
    """Halt/resume events for ``symbol`` with ts in ``[start, end]``, time-ordered."""
    symbol = symbol.upper()
    start_dt = _as_dt(start, end=False)
    end_dt = _as_dt(end, end=True)
    with get_session() as session:
        rows = (
            session.query(HaltEventRow)
            .filter(
                HaltEventRow.symbol == symbol,
                HaltEventRow.ts >= start_dt,
                HaltEventRow.ts <= end_dt,
            )
            .order_by(HaltEventRow.ts.asc())
            .all()
        )
        return [
            HaltEvent(
                symbol=r.symbol,
                ts=_ensure_utc(r.ts),
                state=HaltState(r.state),
                reason_code=r.reason_code,
                source=r.source,
            )
            for r in rows
        ]


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=_UTC) if dt.tzinfo is None else dt.astimezone(_UTC)


def _as_dt(d: datetime | date, *, end: bool) -> datetime:
    if isinstance(d, datetime):
        return _ensure_utc(d)
    from datetime import time

    return datetime.combine(d, time.max if end else time.min, tzinfo=_UTC)
