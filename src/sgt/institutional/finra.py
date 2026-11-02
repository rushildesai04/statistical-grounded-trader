"""FINRA Reg SHO daily short-sale volume (off-exchange, TRF/ADF-reported trades).

Source files ``https://cdn.finra.org/equity/regsho/daily/<PREFIX>shvol<YYYYMMDD>.txt`` are
pipe-delimited ``Date|Symbol|ShortVolume|ShortExemptVolume|TotalVolume|Market`` with a
trailing record-count line. The consolidated NMS file (``CNMS``) exists from 2018-08-01
(verified 2026-11-02: 2018-07-31 and earlier return 403). Before that only per-facility
files exist; they are summed per symbol: FNSQ (Nasdaq TRF Carteret), FNQC (Nasdaq TRF
Chicago, from ~2019), FNYX (NYSE TRF), FNRA (ADF). ``FORF`` (OTC/non-NMS) is excluded
because CNMS excludes it. On 2019-01-02 the facility sum matched CNMS exactly for all
7,862 symbols (total and short volume).

Point-in-time: FINRA publishes the file for day ``d`` after that day's close, so a row is
usable from the open of the next XNYS session: ``available_date = next_session(d)``.
"""
from __future__ import annotations

import gzip
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx
import pandas as pd

from sgt.institutional import RateLimiter, get_with_retry, next_session

log = logging.getLogger(__name__)

BASE_URL = "https://cdn.finra.org/equity/regsho/daily/"
CONSOLIDATED = "CNMS"
FACILITIES = ("FNSQ", "FNQC", "FNYX", "FNRA")
HEADER = ("Date", "Symbol", "ShortVolume", "ShortExemptVolume", "TotalVolume", "Market")
MAX_FILE_BYTES = 32 * 1024 * 1024
# S3/CloudFront answers 403 (not 404) for keys that do not exist.
_ABSENT = frozenset({403, 404})
_SYMBOL = re.compile(r"^[A-Za-z0-9./$+^=-]{1,16}$")
_VOL_COLS = ("short_vol", "short_exempt_vol", "total_vol")
_SHORT_TOL = 1.0  # shares of float slack for fractional-share rounding


class FinraFormatError(ValueError):
    """The file as a whole is unusable (bad header, wrong encoding)."""


@dataclass
class ParseStats:
    rows: int = 0
    rejected: int = 0
    duplicates: int = 0
    trailer_mismatch: bool = False
    reasons: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected += 1
        self.reasons[reason] = self.reasons.get(reason, 0) + 1


@dataclass
class DayResult:
    day: date
    source: str  # "CNMS", "FNSQ+FNYX..." or "missing"
    frame: pd.DataFrame
    stats: ParseStats


def file_url(prefix: str, d: date) -> str:
    return f"{BASE_URL}{prefix}shvol{d:%Y%m%d}.txt"


def _num(s: str) -> float | None:
    try:
        v = float(s)
    except ValueError:
        return None
    return v if math.isfinite(v) and v >= 0 else None


def parse_short_file(raw: bytes, expected: date) -> tuple[pd.DataFrame, ParseStats]:
    """Strictly parse one FINRA short-volume file; bad rows are rejected and counted."""
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise FinraFormatError(f"non-ascii FINRA file for {expected}") from exc
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    while lines and not lines[-1].strip():
        lines.pop()
    if not lines or tuple(lines[0].split("|")) != HEADER:
        raise FinraFormatError(f"unexpected header for {expected}: {lines[0][:120]!r}" if lines
                               else f"empty FINRA file for {expected}")
    body = lines[1:]
    trailer: int | None = None
    if body and body[-1].strip().isdigit():
        trailer = int(body.pop().strip())
    stats = ParseStats()
    want = f"{expected:%Y%m%d}"
    seen: set[str] = set()
    recs: list[tuple[str, float, float, float]] = []
    for ln in body:
        stats.rows += 1
        parts = ln.split("|")
        if len(parts) != len(HEADER):
            stats.reject("field_count")
            continue
        d, sym, sv, sev, tv = (p.strip() for p in parts[:5])
        if d != want:
            stats.reject("date_mismatch")
            continue
        if not _SYMBOL.match(sym):
            stats.reject("bad_symbol")
            continue
        short, exempt, total = _num(sv), _num(sev), _num(tv)
        if short is None or exempt is None or total is None:
            stats.reject("bad_number")
            continue
        if short > total + _SHORT_TOL or exempt > short + _SHORT_TOL:
            stats.reject("short_gt_total")
            continue
        if sym in seen:
            stats.duplicates += 1
            stats.reject("duplicate_symbol")
            continue
        seen.add(sym)
        recs.append((sym, short, exempt, total))
    if trailer is not None and trailer != stats.rows:
        stats.trailer_mismatch = True
    df = pd.DataFrame(recs, columns=["symbol", *_VOL_COLS])
    return df, stats


def _merge_stats(dst: ParseStats, src: ParseStats) -> None:
    dst.rows += src.rows
    dst.rejected += src.rejected
    dst.duplicates += src.duplicates
    dst.trailer_mismatch |= src.trailer_mismatch
    for k, v in src.reasons.items():
        dst.reasons[k] = dst.reasons.get(k, 0) + v


def combine_facilities(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Sum per-facility rows per symbol (equivalent to CNMS; see module docstring)."""
    if not frames:
        return pd.DataFrame(columns=["symbol", *_VOL_COLS])
    return (pd.concat(frames, ignore_index=True)
            .groupby("symbol", as_index=False)[list(_VOL_COLS)].sum())


def _raw_path(raw_dir: Path, prefix: str, d: date) -> Path:
    return raw_dir / f"{d:%Y}" / f"{prefix}shvol{d:%Y%m%d}.txt.gz"


def fetch_raw(client: httpx.Client, prefix: str, d: date, raw_dir: Path,
              limiter: RateLimiter | None = None) -> bytes | None:
    """Raw file bytes from the gzip cache or FINRA; None if FINRA has no such file."""
    path = _raw_path(raw_dir, prefix, d)
    if path.exists():
        return gzip.decompress(path.read_bytes())
    resp = get_with_retry(client, file_url(prefix, d), max_bytes=MAX_FILE_BYTES,
                          absent=_ABSENT, limiter=limiter)
    if resp is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".gz.tmp")
    tmp.write_bytes(gzip.compress(resp.content))
    tmp.replace(path)
    return resp.content


def load_day(client: httpx.Client, d: date, raw_dir: Path,
             limiter: RateLimiter | None = None) -> DayResult:
    """One trading day: CNMS if published, else the facility sum, else ``missing``."""
    raw = fetch_raw(client, CONSOLIDATED, d, raw_dir, limiter)
    if raw is not None:
        df, stats = parse_short_file(raw, d)
        return DayResult(d, CONSOLIDATED, df, stats)
    frames, used, stats = [], [], ParseStats()
    for prefix in FACILITIES:
        raw = fetch_raw(client, prefix, d, raw_dir, limiter)
        if raw is None:
            continue
        df, st = parse_short_file(raw, d)
        _merge_stats(stats, st)
        if not df.empty:
            frames.append(df)
            used.append(prefix)
    if not used:
        return DayResult(d, "missing", combine_facilities([]), stats)
    return DayResult(d, "+".join(used), combine_facilities(frames), stats)


def finalize_day(res: DayResult) -> pd.DataFrame:
    """Output schema for one day, with the point-in-time ``available_date``."""
    df = res.frame.copy()
    df.insert(0, "date", pd.Timestamp(res.day))
    df["source"] = res.source
    df["available_date"] = pd.Timestamp(next_session(res.day))
    for c in _VOL_COLS:
        df[c] = df[c].astype("float64")
    return df[["date", "symbol", *_VOL_COLS, "source", "available_date"]]
