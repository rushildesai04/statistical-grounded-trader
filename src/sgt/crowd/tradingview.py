"""Storage, candidate-source queries, and the golden-check indicator map for
TradingView alert-webhook deliveries.

TradingView alerts serve two purposes (see docs/runbooks/tradingview.md):

1. A live signal/candidate source, mirroring Telegram alerts: symbols seen in
   recent alerts feed the scanner/watchlist via :func:`recent_tradingview_symbols`.
2. "Golden" indicator checks: an alert may carry TradingView's own EMA/MACD/RSI/
   VWAP values; :data:`INDICATOR_MAP` names how each maps to one of our indicator
   outputs so ``scripts/tradingview_golden_check.py`` can recompute ours from the
   Parquet bar store and compare.

All timestamps are tz-aware UTC. The webhook shared secret is verified and discarded
at the boundary; it never reaches this module.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from sgt.core.interfaces import IndicatorSpec
from sgt.core.types import Bar
from sgt.crowd.tradingview_models import TradingViewAlert
from sgt.indicators.registry import IndicatorBank

# --------------------------------------------------------------------------- bounds

MAX_INDICATORS = 32          # hard cap on indicator entries per alert (matches webhook schema).
MAX_INDICATOR_JSON = 8192    # defensive bound when re-reading stored JSON.


# --------------------------------------------------------------------------- golden-check map
#
# name (as the user writes it in the TradingView alert JSON) -> (our spec, output).
# Keep this table in sync with docs/runbooks/tradingview.md. Output names come from
# sgt.indicators.registry.KINDS (e.g. macd outputs line/signal/hist, vwap outputs value).
INDICATOR_MAP: dict[str, tuple[IndicatorSpec, str]] = {
    "ema9": (IndicatorSpec.of("ema", n=9), "value"),
    "ema20": (IndicatorSpec.of("ema", n=20), "value"),
    "ema50": (IndicatorSpec.of("ema", n=50), "value"),
    "ema200": (IndicatorSpec.of("ema", n=200), "value"),
    "sma20": (IndicatorSpec.of("sma", n=20), "value"),
    "sma50": (IndicatorSpec.of("sma", n=50), "value"),
    "rsi14": (IndicatorSpec.of("rsi", n=14), "value"),
    "macd_line": (IndicatorSpec.of("macd", fast=12, slow=26, signal=9), "line"),
    "macd_signal": (IndicatorSpec.of("macd", fast=12, slow=26, signal=9), "signal"),
    "macd_hist": (IndicatorSpec.of("macd", fast=12, slow=26, signal=9), "hist"),
    "vwap": (IndicatorSpec.of("vwap", anchor="session"), "value"),
}


# --------------------------------------------------------------------------- storage


def store_alert(
    session: Session,
    *,
    alert_name: str,
    ticker: str,
    ts: datetime,
    exchange: str | None,
    interval: str | None,
    close: float | None,
    volume: float | None,
    indicators: Mapping[str, float] | None,
    received_at: datetime,
) -> bool:
    """Idempotently persist one alert delivery keyed by (alert_name, ticker, ts).

    Returns ``True`` when a new row was created, ``False`` when the (alert_name,
    ticker, bar time) row already existed (a TradingView retry for the same bar).
    Callers pass already-validated, bounded values; the secret is never passed in.
    """
    _require_utc("ts", ts)
    _require_utc("received_at", received_at)
    ticker = ticker.upper()

    existing = session.get(TradingViewAlert, (alert_name, ticker, ts))
    if existing is not None:
        return False

    indicators_json: str | None = None
    if indicators:
        if len(indicators) > MAX_INDICATORS:
            raise ValueError(f"too many indicators ({len(indicators)} > {MAX_INDICATORS})")
        indicators_json = json.dumps(
            {str(k): float(v) for k, v in indicators.items()}, separators=(",", ":")
        )

    session.add(
        TradingViewAlert(
            alert_name=alert_name,
            ticker=ticker,
            ts=ts,
            exchange=exchange,
            interval=interval,
            close=close,
            volume=volume,
            indicators_json=indicators_json,
            received_at=received_at,
        )
    )
    session.flush()
    return True


def recent_tradingview_symbols(session: Session, since: datetime) -> list[str]:
    """Distinct tickers with a TradingView alert at bar time >= ``since`` (UTC).

    Mirrors the Telegram-alert symbol feed for the scanner/watchlist. Sorted for
    determinism.
    """
    _require_utc("since", since)
    rows = session.execute(
        select(TradingViewAlert.ticker)
        .where(TradingViewAlert.ts >= since)
        .distinct()
    ).scalars().all()
    return sorted(rows)


# --------------------------------------------------------------------------- golden recompute


def point_in_time_snapshot(
    bars: Sequence[Bar], specs: Sequence[IndicatorSpec]
) -> dict[str, float | None]:
    """Stream ``specs`` over ``bars`` (time-ordered) and return the final snapshot.

    The snapshot is keyed ``"<spec.key>.<output>"``. Callers slice ``bars`` so the
    last element is the target bar, giving the exact point-in-time value the live
    engine would have produced — the same code path, no look-ahead.
    """
    bank = IndicatorBank(specs)
    prev_ts: datetime | None = None
    for bar in bars:
        if prev_ts is not None and bar.ts <= prev_ts:
            raise ValueError(f"bars must be strictly increasing in ts: {prev_ts} then {bar.ts}")
        prev_ts = bar.ts
        bank.update(bar)
    return bank.snapshot()


# --------------------------------------------------------------------------- helpers


def _require_utc(name: str, ts: datetime) -> None:
    if ts.tzinfo is None or ts.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"{name} must be tz-aware UTC, got {ts!r}")


def load_indicators(raw: str | None) -> dict[str, float]:
    """Parse a stored ``indicators_json`` string into a bounded ``{name: float}`` map.

    Hostile-input safe: bounded size, strict object-of-numbers shape, entry cap.
    """
    if not raw:
        return {}
    if len(raw) > MAX_INDICATOR_JSON:
        raise ValueError(f"indicators_json too large ({len(raw)} bytes)")
    obj = json.loads(raw)
    if not isinstance(obj, dict):
        raise ValueError("indicators_json must decode to an object")
    if len(obj) > MAX_INDICATORS:
        raise ValueError(f"too many indicators ({len(obj)} > {MAX_INDICATORS})")
    out: dict[str, float] = {}
    for k, v in obj.items():
        if not isinstance(k, str) or isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError(f"bad indicator entry {k!r}={v!r}")
        out[k] = float(v)
    return out
