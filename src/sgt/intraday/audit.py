"""Data audit: quantify what a bar source actually returns before research depends
on it (docs/IMPLEMENTATION_PLAN.md §4.2).

Measures, per symbol: sessions covered, first/last bar, extended-hours coverage
share, and missing-minute rate within regular trading hours. ``compare_sources``
measures close-price and volume agreement between two sources on overlapping
minutes (e.g. Alpaca vs yfinance) to catch vendor disagreement/survivorship gaps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone

from sgt.core.types import Bar, Segment
from sgt.intraday.calendar import SessionCalendar, default_calendar

_UTC = timezone.utc


@dataclass(frozen=True, slots=True)
class SymbolAudit:
    symbol: str
    n_bars: int
    sessions_covered: int
    first_bar: datetime | None
    last_bar: datetime | None
    extended_hours_share: float          # (pre + post bars) / total bars
    rth_missing_minute_rate: float       # missing RTH minutes / expected RTH minutes
    # Share of bars reporting zero volume, per segment. yfinance reports 0 volume
    # for nearly all extended-hours bars (2026-10-12 finding F-2), which silently
    # disables volume signals there; this makes the gap visible in every audit.
    zero_volume_share_pre: float = 0.0
    zero_volume_share_rth: float = 0.0
    zero_volume_share_post: float = 0.0


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    n_overlap: int
    close_mean_abs_pct: float
    close_agree_pct: float               # share within ``close_tol_pct``
    volume_mean_abs_pct: float
    volume_agree_pct: float              # share within ``volume_tol_pct``
    close_tol_pct: float
    volume_tol_pct: float


@dataclass(frozen=True, slots=True)
class AuditReport:
    source_name: str
    start: date
    end: date
    per_symbol: tuple[SymbolAudit, ...] = field(default_factory=tuple)

    def to_markdown(self) -> str:
        lines = [
            f"# Data audit — `{self.source_name}`",
            "",
            f"Range: {self.start.isoformat()} … {self.end.isoformat()}",
            "",
            "| Symbol | Bars | Sessions | First bar (UTC) | Last bar (UTC) | Ext-hours share | RTH missing-minute rate | Zero-volume share pre / rth / post |",
            "|---|---:|---:|---|---|---:|---:|---:|",
        ]
        for s in self.per_symbol:
            fb = s.first_bar.isoformat() if s.first_bar else "—"
            lb = s.last_bar.isoformat() if s.last_bar else "—"
            lines.append(
                f"| {s.symbol} | {s.n_bars} | {s.sessions_covered} | {fb} | {lb} | "
                f"{s.extended_hours_share:.1%} | {s.rth_missing_minute_rate:.1%} | "
                f"{s.zero_volume_share_pre:.0%} / {s.zero_volume_share_rth:.0%} / {s.zero_volume_share_post:.0%} |"
            )
        return "\n".join(lines) + "\n"


def _to_dt(d: date | datetime, *, end: bool) -> datetime:
    if isinstance(d, datetime):
        return d.astimezone(_UTC)
    # whole-day bounds in UTC; end is exclusive next-midnight so the end date is included
    t = time.max if end else time.min
    return datetime.combine(d, t, tzinfo=_UTC)


def _as_date(d: date | datetime) -> date:
    return d.date() if isinstance(d, datetime) else d


def _audit_symbol(symbol: str, bars: list[Bar], cal: SessionCalendar) -> SymbolAudit:
    bars = sorted(bars, key=lambda b: b.ts)
    n = len(bars)
    if n == 0:
        return SymbolAudit(symbol, 0, 0, None, None, 0.0, 0.0)
    sessions = sorted({b.session_date for b in bars})
    ext = sum(1 for b in bars if b.segment in (Segment.PRE, Segment.POST))
    rth_present = sum(1 for b in bars if b.segment is Segment.RTH)
    expected_rth = 0
    for d in sessions:
        b = cal.bounds(d)
        expected_rth += int((b.rth_close - b.rth_open).total_seconds() // 60)
    missing = max(0, expected_rth - rth_present)
    rate = (missing / expected_rth) if expected_rth else 0.0

    def _zero_share(seg: Segment) -> float:
        seg_bars = [b for b in bars if b.segment is seg]
        return (sum(1 for b in seg_bars if b.volume == 0) / len(seg_bars)) if seg_bars else 0.0

    return SymbolAudit(
        symbol=symbol,
        n_bars=n,
        sessions_covered=len(sessions),
        first_bar=bars[0].ts,
        last_bar=bars[-1].ts,
        extended_hours_share=ext / n,
        rth_missing_minute_rate=rate,
        zero_volume_share_pre=_zero_share(Segment.PRE),
        zero_volume_share_rth=_zero_share(Segment.RTH),
        zero_volume_share_post=_zero_share(Segment.POST),
    )


def audit_source(source, symbols, start: date | datetime, end: date | datetime) -> AuditReport:
    """Fetch ``symbols`` over ``[start, end]`` from ``source`` and summarize coverage."""
    cal = default_calendar()
    start_dt = _to_dt(start, end=False)
    end_dt = _to_dt(end, end=True)
    per: list[SymbolAudit] = []
    for sym in symbols:
        bars = source.fetch(sym.upper(), start_dt, end_dt)
        per.append(_audit_symbol(sym.upper(), bars, cal))
    return AuditReport(
        source_name=getattr(source, "name", type(source).__name__),
        start=_as_date(start),
        end=_as_date(end),
        per_symbol=tuple(per),
    )


def compare_sources(
    a_bars: list[Bar],
    b_bars: list[Bar],
    *,
    close_tol_pct: float = 0.5,
    volume_tol_pct: float = 10.0,
) -> ComparisonReport:
    """Agreement between two sources on minutes present in both (keyed by symbol+ts)."""
    a = {(b.symbol, b.ts): b for b in a_bars}
    bmap = {(b.symbol, b.ts): b for b in b_bars}
    keys = sorted(set(a) & set(bmap))
    if not keys:
        return ComparisonReport(0, 0.0, 0.0, 0.0, 0.0, close_tol_pct, volume_tol_pct)
    close_diffs: list[float] = []
    vol_diffs: list[float] = []
    for k in keys:
        pa, pb = a[k].close, bmap[k].close
        close_diffs.append(abs(pa - pb) / pb * 100.0 if pb else 0.0)
        va, vb = a[k].volume, bmap[k].volume
        if vb:
            vol_diffs.append(abs(va - vb) / vb * 100.0)
        else:
            # One side zero and the other not is total disagreement (e.g. yfinance's
            # zero extended-hours volume), never "perfect agreement" (review note R-4).
            vol_diffs.append(0.0 if va == 0 else 100.0)
    n = len(keys)
    return ComparisonReport(
        n_overlap=n,
        close_mean_abs_pct=sum(close_diffs) / n,
        close_agree_pct=sum(1 for d in close_diffs if d <= close_tol_pct) / n,
        volume_mean_abs_pct=sum(vol_diffs) / n,
        volume_agree_pct=sum(1 for d in vol_diffs if d <= volume_tol_pct) / n,
        close_tol_pct=close_tol_pct,
        volume_tol_pct=volume_tol_pct,
    )
