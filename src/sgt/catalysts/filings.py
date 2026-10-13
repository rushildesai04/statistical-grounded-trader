"""SEC EDGAR filings + shares-outstanding collector.

Sources:
- ticker -> CIK map: https://www.sec.gov/files/company_tickers.json
- submissions:        https://data.sec.gov/submissions/CIK{cik:010d}.json
- company facts:      https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json

EDGAR's fair-access policy requires a descriptive ``User-Agent`` (``settings.sec_user_agent``)
and a client-side rate limit; we cap at <= 8 requests/second. Every request has a
timeout and a response-size bound. Dilution-relevant forms are flagged for the
reference layer; shares-outstanding observations are stored point-in-time.
"""
from __future__ import annotations

import json

import logging
import time as _time
from datetime import date, datetime, timezone
from typing import Any, Callable

import httpx

from sgt.catalysts.models import SecFilingRow, SharesSnapshotRow
from sgt.config import get_settings
from sgt.core.http import ResponseTooLarge, get_bounded
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert

log = logging.getLogger(__name__)
_UTC = timezone.utc

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

DILUTION_FORMS: frozenset[str] = frozenset(
    {
        "S-1", "S-1/A", "S-3", "S-3/A", "F-1", "F-1/A", "F-3", "F-3/A",
        "424B1", "424B2", "424B3", "424B4", "424B5", "424B7", "424B8",
    }
)

_MIN_INTERVAL_S = 1.0 / 8.0
_MAX_BYTES = 100 * 1024 * 1024
_MAX_RETRIES = 4
_BACKOFF_BASE_S = 0.5
_DEI_TAG = "EntityCommonStockSharesOutstanding"
_SHARES_SOURCE = "edgar_dei"


class EdgarError(RuntimeError):
    pass


class EdgarAuthError(EdgarError):
    """403/401 — usually a missing/blocked User-Agent. Never retried."""


def _parse_filed_at(acceptance: str, filing_date: str) -> datetime:
    acceptance = (acceptance or "").strip()
    if acceptance:
        try:
            dt = datetime.fromisoformat(acceptance.replace("Z", "+00:00"))
            return dt.astimezone(_UTC) if dt.tzinfo else dt.replace(tzinfo=_UTC)
        except ValueError:
            pass
    return datetime.fromisoformat(filing_date).replace(tzinfo=_UTC)


def _parse_date(s: str) -> datetime:
    return datetime.fromisoformat(s.strip()).replace(tzinfo=_UTC)


class FilingsCollector:
    name = "edgar"

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        user_agent: str | None = None,
        min_interval_s: float = _MIN_INTERVAL_S,
        sleep: Callable[[float], None] = _time.sleep,
        clock: Callable[[], float] = _time.monotonic,
    ) -> None:
        settings = get_settings()
        self.user_agent = user_agent or settings.sec_user_agent
        self.timeout = settings.http_timeout_s
        self._client = client or httpx.Client(timeout=self.timeout)
        self._owns_client = client is None
        self._min_interval = min_interval_s
        self._sleep = sleep
        self._clock = clock
        self._last_req = clock() - min_interval_s
        self._tickers: dict[str, int] | None = None

    # -- transport ----------------------------------------------------------
    def _throttle(self) -> None:
        wait = self._min_interval - (self._clock() - self._last_req)
        if wait > 0:
            self._sleep(wait)
        self._last_req = self._clock()

    def _get_json(self, url: str) -> Any:
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip, deflate"}
        for attempt in range(_MAX_RETRIES + 1):
            self._throttle()
            try:
                resp = get_bounded(self._client, url, max_bytes=_MAX_BYTES,
                                   headers=headers, timeout=self.timeout)
            except ResponseTooLarge as exc:
                raise EdgarError(f"EDGAR response too large (> {_MAX_BYTES} bytes): {url}") from exc
            sc = resp.status_code
            if sc == 200:
                return json.loads(resp.content)
            if sc in (401, 403):
                raise EdgarAuthError(f"EDGAR auth/UA rejected: HTTP {sc} for {url}")
            if sc == 404:
                raise EdgarError(f"EDGAR not found: {url}")
            if sc == 429 or 500 <= sc < 600:
                if attempt < _MAX_RETRIES:
                    self._sleep(_BACKOFF_BASE_S * (2 ** attempt))
                    continue
                raise EdgarError(f"EDGAR unavailable after {attempt + 1} tries: HTTP {sc}")
            raise EdgarError(f"EDGAR request failed: HTTP {sc} for {url}")
        raise EdgarError("EDGAR retries exhausted")  # pragma: no cover

    # -- lookups ------------------------------------------------------------
    def _load_tickers(self) -> dict[str, int]:
        if self._tickers is None:
            data = self._get_json(TICKERS_URL)
            mapping: dict[str, int] = {}
            rows = data.values() if isinstance(data, dict) else data
            for row in rows:
                try:
                    mapping[str(row["ticker"]).upper()] = int(row["cik_str"])
                except (KeyError, TypeError, ValueError):
                    continue
            self._tickers = mapping
        return self._tickers

    def cik_for(self, ticker: str) -> int | None:
        return self._load_tickers().get(ticker.upper())

    # -- collection ---------------------------------------------------------
    def collect(self, ticker: str) -> dict[str, int]:
        """Collect filings + shares snapshots for one ticker. Returns row counts."""
        ticker = ticker.upper()
        cik = self.cik_for(ticker)
        if cik is None:
            raise EdgarError(f"no CIK for ticker {ticker!r}")
        n_filings = self._collect_filings(ticker, cik)
        n_shares = self._collect_shares(ticker, cik)
        return {"filings": n_filings, "shares_snapshots": n_shares}

    def _collect_filings(self, ticker: str, cik: int) -> int:
        data = self._get_json(SUBMISSIONS_URL.format(cik=cik))
        recent = ((data.get("filings") or {}).get("recent")) or {}
        forms = recent.get("form") or []
        dates = recent.get("filingDate") or []
        acc = recent.get("accessionNumber") or []
        acc_dt = recent.get("acceptanceDateTime") or []
        n = 0
        with get_session() as session:
            for i, form in enumerate(forms):
                accession = acc[i] if i < len(acc) else None
                if not accession:
                    continue
                try:
                    filed_at = _parse_filed_at(
                        acc_dt[i] if i < len(acc_dt) else "",
                        dates[i] if i < len(dates) else "",
                    )
                except (ValueError, IndexError):
                    continue
                _upsert(
                    session,
                    SecFilingRow,
                    {
                        "accession": str(accession),
                        "cik": cik,
                        "ticker": ticker,
                        "form": str(form),
                        "filed_at": filed_at,
                    },
                    ["accession"],
                )
                n += 1
        return n

    def _collect_shares(self, ticker: str, cik: int) -> int:
        data = self._get_json(COMPANYFACTS_URL.format(cik=cik))
        try:
            units = data["facts"]["dei"][_DEI_TAG]["units"]["shares"]
        except (KeyError, TypeError):
            return 0
        if not isinstance(units, list):
            return 0
        n = 0
        with get_session() as session:
            for obs in units:
                if not isinstance(obs, dict):
                    continue
                try:
                    value = float(obs["val"])
                    period_end = _parse_date(str(obs["end"]))
                    filed_date = _parse_date(str(obs["filed"]))
                except (KeyError, TypeError, ValueError):
                    continue
                _upsert(
                    session,
                    SharesSnapshotRow,
                    {
                        "ticker": ticker,
                        "period_end": period_end,
                        "filed_date": filed_date,
                        "source": _SHARES_SOURCE,
                        "value": value,
                    },
                    ["ticker", "period_end", "filed_date", "source"],
                )
                n += 1
        return n

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
