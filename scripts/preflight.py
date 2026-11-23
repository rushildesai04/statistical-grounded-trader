"""Pre-market health check (2026-11-08): text the owner BEFORE the open only when something would cost a trading day.

    python scripts/preflight.py          # 09:20 ET weekdays on the runner; silent when all checks pass

Checks: the fade listener's heartbeat (it ticks around the clock), Alpaca market data (a fresh SPY print), the ops
database (writable) and free disk on the volume. Each failure becomes one plain-English line in a single text.

Switches (2026-11-17): a book that is switched off, or a paused system, is not a fault, but it is said before the open
every session it stays that way. On 2026-11-16 both alert books sat off all morning, 26 alerts came in, no text went
out and the owner read the silent phone as a failure ("i havent gotten a single alert yet, something is wrong").
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
MAX_BACK_DAYS = 10                   # days searched back for the latest regular session
MIN_FREE = 0.10


def check_heartbeat(db: OpsStore, now: datetime) -> str | None:
    beat = db.flag("fade_heartbeat")
    if not beat:
        return "the fade listener has never reported (no heartbeat)"
    age = now - datetime.fromisoformat(beat)
    if age > HEARTBEAT_STALE:
        return f"the fade listener is DOWN (last heartbeat {int(age.total_seconds() // 60)} min ago): alerts will be missed"
    return None


def oldest_ok(now: datetime, cal=None) -> datetime:
    """The oldest SPY print that still means "market data works".

    In regular hours: 15 minutes old. Outside them, where the 09:20 run sits, the free feed's last eligible print can
    be far older than that on a healthy morning (2026-11-17: this check texted "market data looks stale" on 6 of the
    last 7 mornings, every one a day on which entries were priced normally), so the newest print that MUST exist is
    the end of the latest regular session: 15 minutes before that close. A dead feed or a bad key still fails: it
    raises, returns nothing, or returns a print from before that close. No session in the last 10 days -> the strict
    15 minutes (fail towards the warning)."""
    if cal is None:
        from sgt.intraday.calendar import default_calendar

        cal = default_calendar()
    from zoneinfo import ZoneInfo

    d = now.astimezone(ZoneInfo("America/New_York")).date()
    for _ in range(MAX_BACK_DAYS):
        if cal.is_session(d):
            b = cal.bounds(d)
            if b.rth_open <= now < b.rth_close:
                return now - DATA_STALE
            if now >= b.rth_close:
                return b.rth_close - DATA_STALE
        d -= timedelta(days=1)
    return now - DATA_STALE


def check_data(quote_time, now: datetime, cal=None) -> str | None:
    """``quote_time()`` -> datetime of the latest SPY trade, or raises."""
    try:
        at = quote_time()
    except Exception as exc:  # noqa: BLE001 - any failure is the finding; reported in plain words
        return f"market data is not responding ({type(exc).__name__}): entries cannot be priced"
    try:
        floor = oldest_ok(now, cal)
    except Exception:  # noqa: BLE001 - no calendar: the strict rule, never a silent pass
        log.exception("preflight: session calendar failed; using the strict freshness rule")
        floor = now - DATA_STALE
    if at is None or at < floor:
        return "market data looks stale (no recent SPY trade): entries may be priced wrong"
    return None


ALERTS_STALE = timedelta(minutes=15)  # outside alert hours the channel is read every 5 minutes


def check_alerts(db: OpsStore, now: datetime, since: datetime | None = None) -> list[str]:
    """The alert intake before the open (2026-11-23): is the channel being read, did the catch-up finish, was any
    stretch of the channel passed unhandled since the previous pre-open check (``since``; four days when there was
    none, so a long weekend is covered). This check runs from the runner's own schedule (09:20 ET), so it does not
    depend on anyone's session being open. Never raises."""
    out: list[str] = []
    try:
        import json

        from sgt.ops import alert_log, own_thread

        age = own_thread.beat_age(Path(db.path).parent, "alerts", now.timestamp())
        if age is not None and age > ALERTS_STALE.total_seconds():
            out.append(f"the alert channel has not been read for {int(age // 60)} min: alerts will be missed")
        try:
            st = json.loads(db.flag("alert_catchup") or "{}")
        except ValueError:
            st = {}
        if st and not st.get("ok", True):
            what = f"stopped at message {st['stuck']}" if st.get("stuck") else st.get("error") or "not finished"
            out.append(f"the alert catch-up is not complete ({what}): alerts may be late or missing")
        gaps = alert_log.gaps(db, (since or now - timedelta(days=4)).isoformat())
        if gaps:
            out.append(f"{len(gaps)} stretch{'es' if len(gaps) != 1 else ''} of the alert channel were passed "
                       f"unhandled since the last pre-open check ({gaps[-1]['reason'][:60]})")
    except Exception as exc:  # noqa: BLE001 - the finding, in plain words; the other checks still report
        out.append(f"the alert intake could not be checked ({type(exc).__name__})")
    return out


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


def switches(db: OpsStore) -> list[str]:
    """One line per switch that will keep trades and their texts away today. Reads flags only."""
    from sgt.fast import fade, long_book

    out = []
    if db.paused():
        out.append("everything is PAUSED: no book trades until it is resumed")
    if fade.is_off(db):
        out.append("alert shorts (fade) are OFF: no short trades and no short texts today")
    if long_book.is_off(db):
        out.append("alert longs are measure-only: each setup is texted as seen, none is traded or counted")
    return out


def spy_trade_time() -> datetime | None:
    import pandas as pd
    from fade_live import data_client

    with data_client() as c:
        r = c.get("/v2/stocks/SPY/trades/latest", params={"feed": "iex"})
        r.raise_for_status()
        t = (r.json() or {}).get("trade") or {}
    return pd.Timestamp(str(t.get("t"))).to_pydatetime(warn=False) if t.get("t") else None


def run(db: OpsStore, now: datetime, quote_time=spy_trade_time, disk: Path = Path("."), cal=None) -> list[str]:
    try:                                         # when the previous pre-open check ran (check_db overwrites it below)
        last = datetime.fromisoformat(db.flag("preflight_at") or "")
    except (ValueError, TypeError):
        last = None
    return [p for p in (check_heartbeat(db, now), check_data(quote_time, now, cal), check_db(db, now),
                        check_disk(disk), *check_alerts(db, now, last)) if p]


def message(problems: list[str], now: datetime, off: list[str] | None = None) -> str:
    parts = []
    if problems:
        parts.append("[SGT] Pre-market check found a problem before the open:\n- " + "\n- ".join(problems)
                     + "\nPaper trading continues where it can. Reply STATUS for details.")
    if off:
        parts.append(("" if problems else "[SGT] Before the open: ") + "Switched off today:\n- " + "\n- ".join(off)
                     + "\nThe system is running; these stay off until you say to turn them on.")
    return "\n".join(parts)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    now = datetime.now(timezone.utc)
    from zoneinfo import ZoneInfo

    from sgt.intraday.calendar import default_calendar

    if not default_calendar().is_session(now.astimezone(ZoneInfo("America/New_York")).date()):
        print("preflight: market closed today; nothing to check")
        return 0
    db = OpsStore()
    problems = run(db, now)
    try:
        off = switches(db)
    except Exception as exc:  # noqa: BLE001 - an unreadable switch is itself worth a line, never a silent pass
        off = [f"the on/off switches could not be read ({type(exc).__name__})"]
    if not problems and not off:
        print("preflight: all checks passed")
        return 0
    text = message(problems, now, off)
    sms = Sms()
    try:
        sms.send(text)
    finally:
        sms.close()
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
