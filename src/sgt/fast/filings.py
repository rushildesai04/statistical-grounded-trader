"""SEC filings at alert time for the fade texts (owner request 2026-11-05: news first; offerings are the classic
reason a pumped small cap dumps).

EDGAR submissions (data.sec.gov) list every filing with its acceptance time, so the check is point in time: only
filings ACCEPTED before the alert count. Informational only (no research rule uses it yet): a failure or a missing
SEC contact returns None and the text omits the line instead of implying "no filings".

EDGAR fair access: a User-Agent naming a contact (secret ``sec_contact``, never logged), well under 10 requests/s
(one or two per alert here), timeouts and size bounds on every response.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from sgt.catalysts.filings import DILUTION_FORMS
from sgt.core.secrets import get_secret

log = logging.getLogger(__name__)
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
TICKER_CACHE = Path("reports/fast/sec_tickers.json")
TICKER_TTL = timedelta(hours=24)
LOOKBACK = timedelta(days=30)
MAX_BYTES = 8 * 1024 * 1024
TIMEOUT = 5.0
MAX_FORMS = 8
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_FORM = re.compile(r"^[A-Z0-9][A-Z0-9/\-]{0,15}$")


def _get_json(client: httpx.Client, url: str) -> dict | None:
    """GET a JSON object, bounded in size; None on any HTTP, size or parse failure (logged without headers)."""
    try:
        with client.stream("GET", url) as r:
            if r.status_code != 200:
                log.warning("edgar %s -> %s", url.rsplit("/", 1)[-1], r.status_code)
                return None
            buf = bytearray()
            for chunk in r.iter_bytes():
                buf += chunk
                if len(buf) > MAX_BYTES:
                    log.warning("edgar %s over %d bytes", url.rsplit("/", 1)[-1], MAX_BYTES)
                    return None
        j = json.loads(bytes(buf))
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("edgar %s failed: %s", url.rsplit("/", 1)[-1], type(exc).__name__)
        return None
    return j if isinstance(j, dict) else None


def _tickers(client: httpx.Client, now: datetime, cache: Path | None = None) -> dict[str, int]:
    """Ticker -> CIK, cached on disk for a day (one ~1 MB download per day)."""
    cache = cache or TICKER_CACHE
    try:
        if cache.exists() and now.timestamp() - cache.stat().st_mtime < TICKER_TTL.total_seconds():
            m = json.loads(cache.read_text())
            if isinstance(m, dict) and m:
                return {str(k): int(v) for k, v in m.items()}
    except (OSError, ValueError, TypeError):
        log.warning("sec ticker cache unreadable; refetching")
    j = _get_json(client, TICKERS_URL) or {}
    m = {}
    for row in j.values():
        if isinstance(row, dict) and isinstance(row.get("ticker"), str) and isinstance(row.get("cik_str"), int):
            m[row["ticker"].upper()] = row["cik_str"]
    if m:
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = cache.with_suffix(".tmp")
            tmp.write_text(json.dumps(m))
            tmp.replace(cache)
        except OSError:
            log.warning("sec ticker cache not written")
    return m


def _accepted(s: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def flags(sub: dict, at: datetime, since_8k: datetime | None = None) -> dict:
    """Point-in-time filing flags from an EDGAR submissions object: filings accepted in (at - 30 days, at].
    ``since_8k``: start of 'today' for the fresh 8-K flag (default 16:00 ET of the previous weekday is the caller's
    choice; None = the last 24 hours)."""
    recent = ((sub.get("filings") or {}).get("recent") or {}) if isinstance(sub, dict) else {}
    forms, acc = recent.get("form") or [], recent.get("acceptanceDateTime") or []
    lo, lo8k = at - LOOKBACK, since_8k or at - timedelta(hours=24)
    rows = []
    for f, a in zip(forms, acc):
        t = _accepted(a)
        if t is None or not isinstance(f, str) or not _FORM.match(f) or not (lo < t <= at):
            continue
        rows.append((t, f))
    rows.sort(reverse=True)
    dil = [(t, f) for t, f in rows if f in DILUTION_FORMS]
    out = {"dilution_30d": int(bool(dil)), "dilution_form": dil[0][1] if dil else None,
           "dilution_days": round((at - dil[0][0]).total_seconds() / 86400, 1) if dil else None,
           "eightk_today": int(any(f.startswith(("8-K", "6-K")) and t > lo8k for t, f in rows)),   # 6-K: foreign issuers
           "forms_30d": ",".join(dict.fromkeys(f for _, f in rows))[:120] or None}
    out["forms_30d"] = ",".join(out["forms_30d"].split(",")[:MAX_FORMS]) if out["forms_30d"] else None
    return out


def lookup(symbol: str, at: datetime, since_8k: datetime | None = None, client: httpx.Client | None = None) -> dict | None:
    """Filing flags for ``symbol`` at ``at``; None = unknown (no SEC contact, unknown ticker, EDGAR down)."""
    if not _SYMBOL.match(symbol or ""):
        return None
    contact = get_secret("sec_contact", required=False)
    if not contact:
        return None
    own = client is None
    client = client or httpx.Client(headers={"User-Agent": f"statistical-grounded-trader {contact}"},
                                    timeout=TIMEOUT, follow_redirects=False)
    try:
        t0 = time.monotonic()
        cik = _tickers(client, at).get(symbol.upper().replace(".", "-"))
        if cik is None:
            return None
        sub = _get_json(client, SUBMISSIONS_URL.format(cik=cik))
        if sub is None:
            return None
        out = flags(sub, at, since_8k)
        log.info("edgar %s ok in %.1fs", symbol, time.monotonic() - t0)
        return out
    finally:
        if own:
            client.close()
