"""Daily data-accumulation job.

Free yfinance 1-minute history only reaches back ~30 days (and <= ~8 days per
request), so research history must be captured daily before it expires. Research
runs also fail closed without float data (the universe gate), so newly discovered
symbols need a usable float snapshot the same day.

This module does four things each day, all bounded and idempotent:

1. **Discover** movers from injected screener queries (strictly validated).
2. **Track** every discovered symbol for ``keep_sessions`` trading sessions after
   its LAST discovery (anti-selection-bias: keep capturing quiet days too), plus a
   static always-active watchlist.
3. **Capture** the last ``lookback_sessions`` complete sessions for each active
   symbol into the immutable Parquet store — only sessions not already present.
4. **Float**: backfill SEC share snapshots for symbols that have none, and take a
   point-in-time VENDOR float snapshot (yfinance ``floatShares`` /
   ``sharesOutstanding``) persisted as a :class:`FloatOverrideRow` so it takes
   precedence over the stale EDGAR dei proxy for the day it was observed.

Every external payload is hostile: quotes are validated, counts bounded, per-item
failures logged and counted (never aborting the run), and the immutable store is
never overwritten. Vendor snapshots are only ever written for the current session
(never backdated — that would be look-ahead).
"""
from __future__ import annotations

import logging
import math
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Mapping, Sequence

from sgt.intraday.calendar import ET, SessionCalendar, default_calendar
from sgt.intraday.sources import YFinanceBars
from sgt.intraday.store import ParquetBarStore

log = logging.getLogger(__name__)
_UTC = timezone.utc

VENDOR_FLOAT_SOURCE = "vendor:yfinance"

_DEFAULT_QUERIES = (
    "day_gainers",
    "small_cap_gainers",
    "aggressive_small_caps",
    "most_actives",
    "day_losers",
)

# Bounds on all external inputs (hostile-by-default).
_COUNT_MIN = 1
_COUNT_MAX = 250
_MAX_WATCHLIST = 1000

# Injected-callable signatures.
Screener = Callable[..., Mapping[str, object]]         # (query, count=...) -> {"quotes": [...]}
FloatSource = Callable[[str], tuple[float | None, float | None]]  # symbol -> (float_shares, shares_out)


# --------------------------------------------------------------------------- types


@dataclass(frozen=True, slots=True)
class Discovered:
    symbol: str
    source_query: str
    price: float
    change_pct: float | None


@dataclass(slots=True)
class AccumulationReport:
    now: datetime
    current_session: date
    discovered: int = 0
    newly_tracked: int = 0
    active: int = 0
    sessions_written: int = 0
    bars_written: int = 0
    no_data: int = 0
    float_collected: int = 0
    no_sec_data: int = 0
    vendor_float_collected: int = 0
    vendor_float_missing: int = 0
    planned_sessions: int = 0
    dry_run: bool = False
    errors: dict[str, int] = field(default_factory=dict)

    def error(self, stage: str) -> None:
        self.errors[stage] = self.errors.get(stage, 0) + 1

    def to_markdown(self) -> str:
        mode = "DRY-RUN (no writes)" if self.dry_run else "live"
        errs = ", ".join(f"{k}={v}" for k, v in sorted(self.errors.items())) or "none"
        return (
            f"# Daily accumulation — {self.current_session.isoformat()} ({mode})\n\n"
            f"Ran at {self.now.isoformat()}\n\n"
            "| Metric | Count |\n"
            "|---|---:|\n"
            f"| Discovered quotes | {self.discovered} |\n"
            f"| Newly tracked symbols | {self.newly_tracked} |\n"
            f"| Active symbols | {self.active} |\n"
            f"| Sessions written | {self.sessions_written} |\n"
            f"| Bars written | {self.bars_written} |\n"
            f"| Planned sessions (dry-run) | {self.planned_sessions} |\n"
            f"| No-data fetches | {self.no_data} |\n"
            f"| SEC float collected | {self.float_collected} |\n"
            f"| No SEC data | {self.no_sec_data} |\n"
            f"| Vendor float collected | {self.vendor_float_collected} |\n"
            f"| Vendor float missing/invalid | {self.vendor_float_missing} |\n"
            f"| Errors by stage | {errs} |\n"
        )


# --------------------------------------------------------------------------- helpers


def _num(v: object) -> float | None:
    """Strictly coerce a vendor value to a positive finite float, else None."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f <= 0:
        return None
    return f


def _valid_symbol(raw: object) -> str | None:
    """1–5 uppercase letters, optional ``.A``/``.B`` class suffix. Else None."""
    if not isinstance(raw, str):
        return None
    s = raw.strip().upper()
    base, _, cls = s.partition(".")
    if not (1 <= len(base) <= 5) or not base.isalpha() or not base.isascii():
        return None
    if cls and cls not in ("A", "B"):
        return None
    return s


def _current_session(cal: SessionCalendar, now: datetime) -> date:
    """Most recent trading session on or before ``now`` (ET date)."""
    d = now.astimezone(ET).date()
    for _ in range(14):
        if cal.is_session(d):
            return d
        d -= timedelta(days=1)
    raise ValueError(f"no trading session within 14 days before {now.isoformat()}")


def _forward_sessions(cal: SessionCalendar, d: date, n: int) -> date:
    """The ``n``-th trading session strictly after ``d`` (weekend/holiday-aware)."""
    if n <= 0:
        return d
    cur = d
    count = 0
    for _ in range(n * 10 + 30):
        cur += timedelta(days=1)
        if cal.is_session(cur):
            count += 1
            if count == n:
                return cur
    raise ValueError(f"could not find session #{n} after {d.isoformat()}")


def _recent_sessions(cal: SessionCalendar, now: datetime, lookback: int) -> list[date]:
    """The last ``lookback`` trading sessions on or before ``now`` (ET date), ascending."""
    out: list[date] = []
    d = now.astimezone(ET).date()
    for _ in range(lookback * 5 + 20):
        if len(out) >= lookback:
            break
        if cal.is_session(d):
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


# --------------------------------------------------------------------------- discovery


def discover(
    screener: Screener,
    *,
    queries: Sequence[str] = _DEFAULT_QUERIES,
    count: int = 100,
    price_min: float = 0.5,
    price_max: float = 200.0,
    report: AccumulationReport | None = None,
) -> list[Discovered]:
    """Run each screener query and return strictly validated movers.

    ``screener`` has the signature of :func:`yfinance.screen` — ``(query, count=...)``
    returning ``{"quotes": [...]}``. A failure of one query is logged and counted
    (``errors['discovery']``) but never aborts the run.
    """
    count = max(_COUNT_MIN, min(_COUNT_MAX, int(count)))
    out: list[Discovered] = []
    for query in queries:
        try:
            payload = screener(query, count=count)
        except Exception as exc:  # noqa: BLE001 - one query must never abort the run
            log.warning("screener query %r failed: %s", query, exc)
            if report is not None:
                report.error("discovery")
            continue
        quotes = payload.get("quotes") if isinstance(payload, Mapping) else None
        if not isinstance(quotes, list):
            log.warning("screener query %r returned no quotes list", query)
            if report is not None:
                report.error("discovery")
            continue
        seen: set[str] = set()
        for quote in quotes[:count]:
            if not isinstance(quote, Mapping):
                continue
            symbol = _valid_symbol(quote.get("symbol"))
            price = _num(quote.get("regularMarketPrice"))
            if symbol is None or price is None or symbol in seen:
                continue
            if not (price_min <= price <= price_max):
                continue
            seen.add(symbol)
            out.append(
                Discovered(
                    symbol=symbol,
                    source_query=str(query),
                    price=price,
                    change_pct=_num(quote.get("regularMarketChangePercent"))
                    if quote.get("regularMarketChangePercent") is not None
                    else None,
                )
            )
    return out


# --------------------------------------------------------------------------- entrypoint


def run_daily(
    now: datetime,
    *,
    screener: Screener,
    source=None,
    filings=None,
    float_source: FloatSource | None = None,
    store: ParquetBarStore | None = None,
    calendar: SessionCalendar | None = None,
    sleep: Callable[[float], None] = _time.sleep,
    watchlist: Sequence[str] = (),
    queries: Sequence[str] = _DEFAULT_QUERIES,
    count: int = 100,
    price_min: float = 0.5,
    price_max: float = 200.0,
    keep_sessions: int = 20,
    lookback_sessions: int = 5,
    max_symbols: int = 200,
    pacing_s: float = 0.5,
    collect_sec_float: bool = True,
    collect_vendor_float: bool = True,
    dry_run: bool = False,
) -> AccumulationReport:
    """Run one day of discovery → tracking → capture → float. Idempotent."""
    # Imported here (not at module top) to avoid an import cycle: the stage module
    # imports the shared types/helpers defined above in this module.
    from sgt.intraday import accumulate_stages as stages

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("run_daily requires a tz-aware now")
    now = now.astimezone(_UTC)
    if len(watchlist) > _MAX_WATCHLIST:
        raise ValueError(f"watchlist too large ({len(watchlist)} > {_MAX_WATCHLIST})")
    cal = calendar or default_calendar()
    store = store or ParquetBarStore()
    source = source if source is not None else YFinanceBars(calendar=cal)
    current_session = _current_session(cal, now)
    report = AccumulationReport(now=now, current_session=current_session, dry_run=dry_run)

    discovered = discover(
        screener,
        queries=queries,
        count=count,
        price_min=price_min,
        price_max=price_max,
        report=report,
    )
    report.discovered = len(discovered)

    stages.track(
        discovered,
        cal=cal,
        current_session=current_session,
        keep_sessions=keep_sessions,
        now=now,
        report=report,
        dry_run=dry_run,
    )

    symbols = stages.active_symbols(
        current_session=current_session,
        watchlist=watchlist,
        max_symbols=max_symbols,
    )
    # A dry-run never persisted new tracking rows, so fold discovery in for planning.
    if dry_run:
        present = set(symbols)
        for d in discovered:
            if d.symbol not in present:
                present.add(d.symbol)
                symbols.append(d.symbol)
        symbols = symbols[:max_symbols]
    report.active = len(symbols)

    stages.capture_missing_sessions(
        symbols,
        cal=cal,
        source=source,
        store=store,
        now=now,
        lookback_sessions=lookback_sessions,
        sleep=sleep,
        pacing_s=pacing_s,
        report=report,
        dry_run=dry_run,
    )

    if collect_sec_float and filings is not None:
        stages.backfill_sec_float(symbols, filings=filings, report=report, dry_run=dry_run)
    if collect_vendor_float and float_source is not None:
        stages.snapshot_vendor_float(
            symbols,
            float_source=float_source,
            current_session=current_session,
            now=now,
            sleep=sleep,
            pacing_s=pacing_s,
            report=report,
            dry_run=dry_run,
        )
    return report


# --------------------------------------------------------------------------- yfinance defaults


def default_screener(query: str, *, count: int = 100) -> Mapping[str, object]:
    """Production screener: wraps :func:`yfinance.screen`. Imported lazily so the
    module has no hard yfinance dependency for tests."""
    import yfinance as yf

    result = yf.screen(query, count=count)
    return result if isinstance(result, Mapping) else {"quotes": []}


def default_float_source(symbol: str) -> tuple[float | None, float | None]:
    """Production vendor float: reads yfinance ``floatShares`` / ``sharesOutstanding``.

    Best-effort and bounded by yfinance's own request timeout; any failure is
    logged and surfaced as ``(None, None)`` so the caller counts it as missing
    rather than aborting the run.
    """
    import yfinance as yf

    try:
        info = yf.Ticker(symbol).get_info()
    except Exception as exc:  # noqa: BLE001 - vendor failure must not abort the run
        log.warning("yfinance info fetch failed for %s: %s", symbol, exc)
        return (None, None)
    if not isinstance(info, Mapping):
        return (None, None)
    return (_num(info.get("floatShares")), _num(info.get("sharesOutstanding")))
