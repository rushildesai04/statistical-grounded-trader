"""CUSIP -> US composite ticker via the OpenFIGI v3 mapping API.

Keyless access is limited to 25 requests/minute and 10 jobs/request
(https://www.openfigi.com/api, checked 2026-11-02); an ``openfigi_api_key`` secret, if
present, lifts this to 25 requests / 6 s and 100 jobs. OpenFIGI maps a CUSIP to the FIGI
of the security today: retired CUSIPs (old mergers, delistings) often return no match and
recycled tickers can point to a later issuer, so the map is survivorship-limited.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import pandas as pd

from sgt.core.http import ResponseTooLarge
from sgt.institutional import RETRIES, FetchError, RateLimiter

log = logging.getLogger(__name__)

URL = "https://api.openfigi.com/v3/mapping"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_TICKER = re.compile(r"^[A-Za-z0-9./ -]{1,20}$")
_FIELD_MAX = 200
COLUMNS = ["cusip", "ticker", "ticker_norm", "figi", "name", "exch_code", "security_type",
           "market_sector", "n_candidates", "status", "detail", "mapped_at"]
DONE_STATUSES = frozenset({"mapped", "no_match"})


@dataclass(frozen=True)
class Limits:
    jobs_per_request: int
    requests_per_second: float


KEYLESS = Limits(10, 25 / 62)  # 25/min with a 2 s cushion
KEYED = Limits(100, 25 / 6.5)


def eligible_cusips(agg: pd.DataFrame, min_holders: int = 5) -> list[str]:
    """CUSIPs held by at least ``min_holders`` distinct filers in any period."""
    peak = agg.groupby("cusip")["n_holders"].max()
    return sorted(peak[peak >= min_holders].index)


def _clip(v: object) -> str:
    return v[:_FIELD_MAX] if isinstance(v, str) else ""


def parse_mapping_response(cusips: list[str], payload: object, now: str) -> list[dict]:
    """One output record per requested CUSIP; malformed items become status 'invalid'."""
    if not isinstance(payload, list) or len(payload) != len(cusips):
        raise ValueError("OpenFIGI response is not a list matching the request")
    out = []
    for cusip, item in zip(cusips, payload, strict=True):
        rec = dict.fromkeys(COLUMNS, "")
        rec.update(cusip=cusip, n_candidates=0, mapped_at=now)
        if not isinstance(item, dict):
            rec.update(status="invalid", detail="item not an object")
        elif "data" in item:
            data = [d for d in item["data"] if isinstance(d, dict)] if isinstance(item["data"], list) else []
            eq = [d for d in data if d.get("marketSector") == "Equity" and isinstance(d.get("ticker"), str)
                  and _TICKER.match(d["ticker"])]
            if not eq:
                rec.update(status="no_match", detail="no equity candidate", n_candidates=len(data))
            else:
                d = eq[0]
                rec.update(status="mapped", ticker=d["ticker"], ticker_norm=d["ticker"].replace("/", ".").upper(),
                           figi=_clip(d.get("figi")), name=_clip(d.get("name")),
                           exch_code=_clip(d.get("exchCode")), security_type=_clip(d.get("securityType")),
                           market_sector="Equity", n_candidates=len(eq))
        elif "warning" in item:
            rec.update(status="no_match", detail=_clip(item["warning"]))
        elif "error" in item:
            rec.update(status="error", detail=_clip(item["error"]))
        else:
            rec.update(status="invalid", detail="unrecognised item")
        out.append(rec)
    return out


def _post(client: httpx.Client, body: bytes, headers: dict[str, str],
          limiter: RateLimiter) -> bytes:
    for attempt in range(RETRIES + 1):
        limiter.wait()
        try:
            with client.stream("POST", URL, content=body, headers=headers) as resp:
                chunks, total = [], 0
                for chunk in resp.iter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise ResponseTooLarge(URL, MAX_RESPONSE_BYTES)
                    chunks.append(chunk)
                status, rh = resp.status_code, resp.headers
        except httpx.TransportError as exc:
            if attempt == RETRIES:
                raise FetchError(f"openfigi: {type(exc).__name__}") from exc
            time.sleep(2 ** attempt)
            continue
        if status == 200:
            return b"".join(chunks)
        if status == 429 or status >= 500:
            reset = rh.get("ratelimit-reset", "")
            wait = min(float(reset), 120.0) + 1 if reset.isdigit() else 60.0
            log.warning("openfigi HTTP %d; sleeping %.0fs", status, wait)
            time.sleep(wait)
            continue
        raise FetchError(f"openfigi: HTTP {status}")
    raise FetchError("openfigi: retries exhausted")


def map_cusips(client: httpx.Client, cusips: Iterable[str], *, api_key: str | None,
               on_batch=None) -> list[dict]:
    """Map ``cusips`` in rate-limited batches; ``on_batch(records)`` is called after each."""
    lim = KEYED if api_key else KEYLESS
    limiter = RateLimiter(lim.requests_per_second)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key
    todo, out = list(cusips), []
    for i in range(0, len(todo), lim.jobs_per_request):
        batch = todo[i:i + lim.jobs_per_request]
        jobs = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in batch]
        raw = _post(client, json.dumps(jobs).encode(), headers, limiter)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        try:
            recs = parse_mapping_response(batch, json.loads(raw), now)
        except ValueError as exc:
            log.error("batch %d malformed: %s", i, exc)
            recs = [dict(dict.fromkeys(COLUMNS, ""), cusip=c, status="invalid", detail=str(exc)[:200],
                         n_candidates=0, mapped_at=now) for c in batch]
        out.extend(recs)
        if on_batch is not None:
            on_batch(recs)
    return out


def merge_results(existing: pd.DataFrame | None, new: list[dict]) -> pd.DataFrame:
    """Latest record per CUSIP wins."""
    frames = [f for f in (existing, pd.DataFrame(new, columns=COLUMNS)) if f is not None and len(f)]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["n_candidates"] = df["n_candidates"].astype("int64")
    return df.drop_duplicates("cusip", keep="last").sort_values("cusip").reset_index(drop=True)
