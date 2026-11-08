"""Pre-market health check (2026-11-08): text the owner BEFORE the open only when something would cost a trading day.

    python scripts/preflight.py          # 09:20 ET weekdays on the runner; silent when all checks pass

Checks: the fade listener's heartbeat (it ticks around the clock), Alpaca market data (a fresh SPY print), the ops
database (writable) and free disk on the volume. Each failure becomes one plain-English line in a single text.
"""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sgt.ops.sms import Sms
from sgt.ops.store import OpsStore

log = logging.getLogger("preflight")
HEARTBEAT_STALE = timedelta(minutes=15)
DATA_STALE = timedelta(minutes=15)
MIN_FREE = 0.10


def check_heartbeat(db: OpsStore, now: datetime) -> str | None:
    beat = db.flag("fade_heartbeat")
    if not beat:
        return "the fade listener has never reported (no heartbeat)"
    age = now - datetime.fromisoformat(beat)
    if age > HEARTBEAT_STALE:
        return f"the fade listener is DOWN (last heartbeat {int(age.total_seconds() // 60)} min ago): alerts will be missed"
    return None


def check_data(quote_time, now: datetime) -> str | None:
    """``quote_time()`` -> datetime of the latest SPY trade, or raises."""
    try:
        at = quote_time()
    except Exception as exc:  # noqa: BLE001 - any failure is the finding; reported in plain words
        return f"market data is not responding ({type(exc).__name__}): entries cannot be priced"
    if at is None or now - at > DATA_STALE:
        return "market data looks stale (no recent SPY trade): entries may be priced wrong"
    return None


def check_db(db: OpsStore, now: datetime) -> str | None:
    try:
        db.set_flag("preflight_at", now.isoformat())
    except Exception as exc:  # noqa: BLE001
        return f"the trade database is not writable ({type(exc).__name__}): nothing can be recorded"
    return None


def check_disk(path: Path) -> str | None:
    u = shutil.disk_usage(path)
    free = u.free / u.total
    return None if free >= MIN_FREE else f"the server disk is {1 - free:.0%} full: jobs may start failing"


def spy_trade_time() -> datetime | None:
    import pandas as pd
    from fade_live import data_client

    with data_client() as c:
        r = c.get("/v2/stocks/SPY/trades/latest", params={"feed": "iex"})
        r.raise_for_status()
        t = (r.json() or {}).get("trade") or {}
    return pd.Timestamp(str(t.get("t"))).to_pydatetime(warn=False) if t.get("t") else None


def run(db: OpsStore, now: datetime, quote_time=spy_trade_time, disk: Path = Path(".")) -> list[str]:
    return [p for p in (check_heartbeat(db, now), check_data(quote_time, now), check_db(db, now), check_disk(disk))
            if p]


def message(problems: list[str], now: datetime) -> str:
    return ("[SGT] Pre-market check found a problem before the open:\n- " + "\n- ".join(problems)
            + "\nPaper trading continues where it can. Reply STATUS for details.")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    now = datetime.now(timezone.utc)
    from zoneinfo import ZoneInfo

    from sgt.intraday.calendar import default_calendar

    if not default_calendar().is_session(now.astimezone(ZoneInfo("America/New_York")).date()):
        print("preflight: market closed today; nothing to check")
        return 0
    problems = run(OpsStore(), now)
    if not problems:
        print("preflight: all checks passed")
        return 0
    text = message(problems, now)
    sms = Sms()
    try:
        sms.send(text)
    finally:
        sms.close()
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
