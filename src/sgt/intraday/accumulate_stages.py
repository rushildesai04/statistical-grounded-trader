"""Per-stage workers for the daily accumulation job (:mod:`sgt.intraday.accumulate`).

Split out so each module stays focused and under the repo's size limit. Shared
types, bounds and the session-date helpers live in ``accumulate``; this module
holds the four stages that read/write persistence: tracking, active-symbol
selection, bar capture, and SEC + vendor float collection.

Every stage is bounded and idempotent, treats vendor payloads as hostile, logs and
counts per-item failures instead of aborting, and never overwrites the immutable
bar store or backdates a vendor float snapshot.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from typing import Callable, Sequence

from sqlalchemy import select

from sgt.catalysts.filings import EdgarAuthError, EdgarError
from sgt.catalysts.models import FloatOverrideRow, SharesSnapshotRow
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert
from sgt.intraday.accumulate import (
    VENDOR_FLOAT_SOURCE,
    AccumulationReport,
    Discovered,
    FloatSource,
    _forward_sessions,
    _num,
    _recent_sessions,
    _valid_symbol,
)
from sgt.intraday.calendar import SessionCalendar
from sgt.intraday.models import TrackedSymbolRow
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger(__name__)
_UTC = timezone.utc


def track(
    discovered: list[Discovered],
    *,
    cal: SessionCalendar,
    current_session: date,
    keep_sessions: int,
    now: datetime,
    report: AccumulationReport,
    dry_run: bool,
) -> None:
    """Upsert a tracking row per discovered symbol, extending its active window."""
    by_symbol: dict[str, set[str]] = {}
    for d in discovered:
        by_symbol.setdefault(d.symbol, set()).add(d.source_query)
    if not by_symbol:
        return
    cur = current_session.isoformat()
    active_until = _forward_sessions(cal, current_session, keep_sessions).isoformat()
    with get_session() as session:
        existing = {
            row.symbol: row
            for row in session.query(TrackedSymbolRow)
            .filter(TrackedSymbolRow.symbol.in_(list(by_symbol)))
            .all()
        }
        for symbol, sources in by_symbol.items():
            prior = existing.get(symbol)
            if prior is None:
                report.newly_tracked += 1
                merged = sorted(sources)
                first_seen = cur
                created = now
            else:
                merged = sorted(set(prior.sources or []) | sources)
                first_seen = prior.first_seen_session
                created = prior.created_at
            if dry_run:
                continue
            _upsert(
                session,
                TrackedSymbolRow,
                {
                    "symbol": symbol,
                    "first_seen_session": first_seen,
                    "last_seen_session": cur,
                    "sources": merged,
                    "active_until_session": active_until,
                    "created_at": created,
                },
                ["symbol"],
            )


def active_symbols(
    *,
    current_session: date,
    watchlist: Sequence[str],
    max_symbols: int,
) -> list[str]:
    """Active symbols, most-recently-seen first then symbol, bounded by ``max_symbols``.

    A tracked symbol is active while ``active_until_session >= current_session``.
    Watchlist symbols are always active and sort to the front.
    """
    cur = current_session.isoformat()
    last_seen: dict[str, str] = {}
    with get_session() as session:
        for row in (
            session.query(TrackedSymbolRow)
            .filter(TrackedSymbolRow.active_until_session >= cur)
            .all()
        ):
            last_seen[row.symbol] = row.last_seen_session
    for raw in watchlist:
        sym = _valid_symbol(raw)
        if sym is not None:
            last_seen[sym] = cur  # always active, sorts first
    ordered = sorted(last_seen.items(), key=lambda kv: kv[0])          # symbol asc
    ordered.sort(key=lambda kv: kv[1], reverse=True)                   # last_seen desc (stable)
    return [s for s, _ in ordered[:max_symbols]]


def capture_missing_sessions(
    symbols: list[str],
    *,
    cal: SessionCalendar,
    source,
    store: ParquetBarStore,
    now: datetime,
    lookback_sessions: int,
    sleep: Callable[[float], None],
    pacing_s: float,
    report: AccumulationReport,
    dry_run: bool,
) -> None:
    """Capture every missing, complete session in the lookback window per symbol."""
    recent = _recent_sessions(cal, now, lookback_sessions)
    complete = [d for d in recent if cal.bounds(d).post_close < now]
    for symbol in symbols:
        try:
            present = set(store.sessions(symbol))
        except Exception as exc:  # noqa: BLE001 - store read must not abort the run
            log.warning("store.sessions(%s) failed: %s", symbol, exc)
            report.error("capture")
            continue
        for sdate in complete:
            if sdate in present:
                continue
            report.planned_sessions += 1
            if dry_run:
                continue
            try:
                b = cal.bounds(sdate)
                bars = source.fetch(symbol, b.pre_open, b.post_close)
                sleep(pacing_s)
                sbars = [x for x in bars if x.session_date == sdate]
                if not sbars:
                    report.no_data += 1
                    log.info("no data for %s on %s", symbol, sdate.isoformat())
                    continue
                sbars.sort(key=lambda x: x.ts)
                store.write_session(symbol, sdate, sbars)  # immutable: never overwrite
                report.sessions_written += 1
                report.bars_written += len(sbars)
            except FileExistsError:
                # Raced with another writer; the session is already captured.
                log.info("session %s/%s already present, skipping", symbol, sdate.isoformat())
            except Exception as exc:  # noqa: BLE001 - per-symbol failure logged + counted
                log.warning("capture %s on %s failed: %s", symbol, sdate.isoformat(), exc)
                report.error("capture")


def backfill_sec_float(
    symbols: list[str],
    *,
    filings,
    report: AccumulationReport,
    dry_run: bool,
) -> None:
    """Backfill SEC share snapshots for symbols that have none yet."""
    if dry_run:
        return
    with get_session() as session:
        have = {
            s
            for (s,) in session.query(SharesSnapshotRow.ticker)
            .filter(SharesSnapshotRow.ticker.in_(symbols))
            .distinct()
            .all()
        }
    for symbol in symbols:
        if symbol in have:
            continue
        try:
            result = filings.collect(symbol)
        except EdgarAuthError as exc:
            # UA/auth rejection affects every symbol: surface it as an error and stop the
            # stage instead of mislabelling the whole universe as "no SEC data".
            report.errors["sec_float"] = report.errors.get("sec_float", 0) + 1
            log.error("EDGAR rejected requests (check SGT_SEC_USER_AGENT): %s", exc)
            return
        except EdgarError as exc:
            # No CIK / not in EDGAR (OTC, foreign) is a normal outcome.
            report.no_sec_data += 1
            log.info("no SEC float for %s: %s", symbol, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - transport failure: counted, run continues
            report.errors["sec_float"] = report.errors.get("sec_float", 0) + 1
            log.warning("SEC float fetch failed for %s: %s", symbol, exc)
            continue
        if isinstance(result, dict) and result.get("shares_snapshots", 0) > 0:
            report.float_collected += 1
        else:
            report.no_sec_data += 1  # in EDGAR but no dei share-count facts (e.g. WBUY)


def snapshot_vendor_float(
    symbols: list[str],
    *,
    float_source: FloatSource,
    current_session: date,
    now: datetime,
    sleep: Callable[[float], None],
    pacing_s: float,
    report: AccumulationReport,
    dry_run: bool,
) -> None:
    """Take a point-in-time vendor float snapshot for the CURRENT session only.

    Persisted as a :class:`FloatOverrideRow` effective at the OBSERVATION time
    (``now``), which the reference layer prefers over the stale EDGAR proxy. Stamping
    the session's midnight would let earlier decisions that same day see a value
    observed after the close (same-day look-ahead; 2026-10-13, orchestrator review).
    One snapshot per (symbol, session): a second same-day run is a no-op.
    """
    if dry_run:
        return
    effective = now.astimezone(_UTC)
    day_lo = datetime.combine(current_session, time.min, tzinfo=_UTC)
    day_hi = datetime.combine(current_session, time.max, tzinfo=_UTC)
    for symbol in symbols:
        with get_session() as session:
            already = session.scalar(
                select(FloatOverrideRow.ticker).where(
                    FloatOverrideRow.ticker == symbol,
                    FloatOverrideRow.source == VENDOR_FLOAT_SOURCE,
                    FloatOverrideRow.effective_date >= day_lo,
                    FloatOverrideRow.effective_date <= day_hi,
                ).limit(1))
        if already is not None:
            continue
        try:
            float_shares, shares_out = float_source(symbol)
            sleep(pacing_s)
        except Exception as exc:  # noqa: BLE001 - per-symbol vendor failure logged + counted
            log.warning("vendor float fetch failed for %s: %s", symbol, exc)
            report.vendor_float_missing += 1
            continue
        fs = _num(float_shares)
        so = _num(shares_out)
        free_float = fs if fs is not None else so
        if free_float is None:
            report.vendor_float_missing += 1
            log.info("no usable vendor float for %s", symbol)
            continue
        note = f"float_shares={fs};shares_outstanding={so}"
        with get_session() as session:
            _upsert(
                session,
                FloatOverrideRow,
                {
                    "ticker": symbol,
                    "effective_date": effective,
                    "free_float": free_float,
                    "source": VENDOR_FLOAT_SOURCE,
                    "note": note[:256],
                },
                ["ticker", "effective_date"],
            )
        report.vendor_float_collected += 1
