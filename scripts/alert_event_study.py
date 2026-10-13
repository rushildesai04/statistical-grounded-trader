"""Run the Telegram-alert forward-return event study and print a Markdown report.

Reads parsed alerts from ``crowd_alerts`` and loads 1-minute bars through an
injectable loader ``(symbol, start, end) -> list[Bar]``. By default it uses
``sgt.intraday.store.ParquetBarStore`` (built concurrently); if that module is not
importable the script fails with a clear message rather than guessing.

Usage:
    python scripts/alert_event_study.py
    python scripts/alert_event_study.py --horizons 1,5,15,60 --pad-days 1
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Sequence

from sqlalchemy import select

from sgt.config import get_settings
from sgt.core.types import Bar
from sgt.crowd.event_study import AlertRecord, run_event_study
from sgt.crowd.models import CrowdAlert
from sgt.db.session import get_session, init_db

BarLoader = Callable[[str, date, date], Sequence[Bar]]


def _default_loader() -> BarLoader:
    try:
        from sgt.intraday.store import ParquetBarStore
    except ImportError as exc:
        raise SystemExit(
            "sgt.intraday.store.ParquetBarStore is not available yet; this script "
            "needs the intraday Parquet store. Install/complete that module or pass a "
            "custom loader. Original import error: " + str(exc)
        ) from exc
    store = ParquetBarStore(get_settings().data_dir)

    def _load(symbol: str, start: date, end: date) -> Sequence[Bar]:
        return store.read(symbol, start, end)

    return _load


def _load_alerts() -> list[AlertRecord]:
    with get_session() as session:
        rows = session.execute(
            select(
                CrowdAlert.symbol,
                CrowdAlert.alert_type,
                CrowdAlert.posted_at,
                CrowdAlert.has_news,
                CrowdAlert.price,
            ).order_by(CrowdAlert.posted_at.asc())
        ).all()
    out: list[AlertRecord] = []
    for symbol, alert_type, posted_at, has_news, price in rows:
        ts = posted_at if posted_at.tzinfo else posted_at.replace(tzinfo=timezone.utc)
        out.append(
            AlertRecord(
                symbol=symbol,
                alert_type=alert_type,
                posted_at=ts.astimezone(timezone.utc),
                has_news=has_news,
                price=price,
            )
        )
    return out


def run(loader: BarLoader, *, horizons: tuple[int, ...], pad_days: int) -> str:
    alerts = _load_alerts()
    if not alerts:
        return "No alerts in crowd_alerts; nothing to study."

    spans: dict[str, list[datetime]] = defaultdict(list)
    for a in alerts:
        spans[a.symbol].append(a.posted_at)

    bars_by_symbol: dict[str, Sequence[Bar]] = {}
    for symbol, times in spans.items():
        start = (min(times) - timedelta(days=pad_days)).date()
        end = (max(times) + timedelta(days=pad_days)).date()
        bars_by_symbol[symbol] = list(loader(symbol, start, end))

    result = run_event_study(alerts, bars_by_symbol, horizons=horizons)
    return result.to_markdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Telegram alert event study.")
    parser.add_argument("--horizons", default="1,5,15,60", help="comma-separated minutes")
    parser.add_argument("--pad-days", type=int, default=1, help="calendar days padded around alerts")
    args = parser.parse_args()
    horizons = tuple(int(h) for h in args.horizons.split(",") if h.strip())

    init_db()
    print(run(_default_loader(), horizons=horizons, pad_days=args.pad_days))


if __name__ == "__main__":
    main()
