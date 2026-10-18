"""Golden check: do OUR indicators match TradingView's?

For every stored TradingView alert that carried indicator values (see
``sgt.crowd.tradingview.INDICATOR_MAP`` for the name mapping), recompute the same
indicator from our Parquet bar store at that symbol/bar using the live streaming
code path, and report abs/rel differences with a pass/fail tolerance. Output is
Markdown (stdout or ``--out``). Exit code is non-zero if any comparison fails or an
alert could not be checked, so it can gate CI.

Mapping (kept in sync with docs/runbooks/tradingview.md):
    ema9/ema20/ema50/ema200 -> ema(n=...).value
    sma20/sma50             -> sma(n=...).value
    rsi14                   -> rsi(n=14).value
    macd_line/signal/hist   -> macd(fast=12,slow=26,signal=9).{line,signal,hist}
    vwap                    -> vwap(anchor=session).value

Usage::

    python scripts/tradingview_golden_check.py
    python scripts/tradingview_golden_check.py --since 2026-10-01 --lookback-sessions 3
    python scripts/tradingview_golden_check.py --data-dir data/alpaca --abs-tol 1e-4 --out report.md
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from sgt.config import get_settings
from sgt.crowd.tradingview import INDICATOR_MAP, load_indicators, point_in_time_snapshot
from sgt.crowd.tradingview_models import TradingViewAlert
from sgt.db.session import get_session, init_db
from sgt.intraday.calendar import SessionCalendar, default_calendar
from sgt.intraday.store import ParquetBarStore


@dataclass(frozen=True, slots=True)
class GoldenRow:
    ticker: str
    bar_ts: datetime
    name: str
    tv_value: float | None
    our_value: float | None
    abs_diff: float | None
    rel_diff: float | None
    status: str  # "pass" | "fail" | reason ("no_bar", "no_session", "not_ready", "unknown_name")

    @property
    def ok(self) -> bool:
        return self.status == "pass"


def _compare(tv: float, our: float, abs_tol: float, rel_tol: float) -> tuple[float, float, str]:
    abs_diff = abs(our - tv)
    rel_diff = abs_diff / max(abs(tv), 1e-12)
    ok = abs_diff <= abs_tol or rel_diff <= rel_tol
    return abs_diff, rel_diff, "pass" if ok else "fail"


def run_golden_check(
    session: Session,
    store: ParquetBarStore,
    *,
    since: datetime,
    lookback_sessions: int,
    abs_tol: float,
    rel_tol: float,
    symbol: str | None = None,
    calendar: SessionCalendar | None = None,
) -> list[GoldenRow]:
    """Recompute and compare every mapped indicator for alerts with bar time >= ``since``."""
    if since.tzinfo is None:
        raise ValueError("since must be tz-aware UTC")
    if lookback_sessions < 0:
        raise ValueError("lookback_sessions must be >= 0")
    cal = calendar or default_calendar()

    stmt = select(TradingViewAlert).where(TradingViewAlert.ts >= since)
    if symbol is not None:
        stmt = stmt.where(TradingViewAlert.ticker == symbol.upper())
    stmt = stmt.order_by(TradingViewAlert.ts.asc(), TradingViewAlert.ticker.asc())
    alerts = session.execute(stmt).scalars().all()

    rows: list[GoldenRow] = []
    for alert in alerts:
        indicators = load_indicators(alert.indicators_json)
        mapped = {n: v for n, v in indicators.items() if n in INDICATOR_MAP}
        if not mapped:
            continue
        bar_ts = alert.ts if alert.ts.tzinfo else alert.ts.replace(tzinfo=timezone.utc)
        bar_ts = bar_ts.astimezone(timezone.utc)

        session_date = cal.session_of(bar_ts)
        if session_date is None:
            rows.extend(
                GoldenRow(alert.ticker, bar_ts, n, mapped[n], None, None, None, "no_session")
                for n in sorted(mapped)
            )
            continue

        prior = cal.previous_sessions(session_date, lookback_sessions)
        start = prior[0] if prior else session_date
        bars = store.read(alert.ticker, start, session_date)
        target = next((i for i, b in enumerate(bars) if b.ts == bar_ts), None)
        if target is None:
            rows.extend(
                GoldenRow(alert.ticker, bar_ts, n, mapped[n], None, None, None, "no_bar")
                for n in sorted(mapped)
            )
            continue

        specs = list({INDICATOR_MAP[n][0] for n in mapped})
        snapshot = point_in_time_snapshot(bars[: target + 1], specs)
        for name in sorted(mapped):
            spec, output = INDICATOR_MAP[name]
            our = snapshot.get(f"{spec.key}.{output}")
            tv = mapped[name]
            if our is None:
                rows.append(GoldenRow(alert.ticker, bar_ts, name, tv, None, None, None, "not_ready"))
                continue
            abs_diff, rel_diff, status = _compare(tv, our, abs_tol, rel_tol)
            rows.append(GoldenRow(alert.ticker, bar_ts, name, tv, our, abs_diff, rel_diff, status))
    return rows


def render_markdown(rows: list[GoldenRow], *, abs_tol: float, rel_tol: float) -> str:
    total = len(rows)
    passed = sum(1 for r in rows if r.ok)
    failed = sum(1 for r in rows if r.status == "fail")
    unchecked = total - passed - failed
    lines = [
        "# TradingView golden check",
        "",
        f"- generated: {datetime.now(timezone.utc).isoformat()}",
        f"- tolerance: abs <= {abs_tol:g} OR rel <= {rel_tol:g}",
        f"- comparisons: {total} | pass: {passed} | fail: {failed} | unchecked: {unchecked}",
        "",
        "| ticker | bar (UTC) | indicator | tradingview | ours | abs diff | rel diff | result |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in rows:
        def _f(x: float | None) -> str:
            return "—" if x is None else f"{x:.6g}"

        lines.append(
            f"| {r.ticker} | {r.bar_ts.isoformat()} | {r.name} | {_f(r.tv_value)} | "
            f"{_f(r.our_value)} | {_f(r.abs_diff)} | {_f(r.rel_diff)} | {r.status} |"
        )
    if not rows:
        lines.append("| (no alerts with mapped indicator values) |")
    return "\n".join(lines) + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Compare stored TradingView indicator values with ours.")
    ap.add_argument("--since", default=None, help="only alerts at/after this ISO date/time (default: 30 days ago)")
    ap.add_argument("--symbol", default=None, help="restrict to one ticker")
    ap.add_argument("--data-dir", default=None, help="Parquet store root (default: <data_dir>/alpaca)")
    ap.add_argument("--lookback-sessions", type=int, default=2, help="prior sessions of bars to warm up indicators")
    ap.add_argument("--abs-tol", type=float, default=1e-4, help="absolute tolerance")
    ap.add_argument("--rel-tol", type=float, default=1e-3, help="relative tolerance")
    ap.add_argument("--out", default=None, help="write Markdown here (default: stdout)")
    return ap.parse_args(argv)


def _resolve_since(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(timezone.utc) - timedelta(days=30)
    s = raw.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    since = _resolve_since(args.since)
    root = Path(args.data_dir) if args.data_dir else Path(get_settings().data_dir) / "alpaca"
    store = ParquetBarStore(root)

    init_db()
    with get_session() as session:
        rows = run_golden_check(
            session,
            store,
            since=since,
            lookback_sessions=args.lookback_sessions,
            abs_tol=args.abs_tol,
            rel_tol=args.rel_tol,
            symbol=args.symbol,
        )

    md = render_markdown(rows, abs_tol=args.abs_tol, rel_tol=args.rel_tol)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)

    failed = any(r.status != "pass" for r in rows)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
