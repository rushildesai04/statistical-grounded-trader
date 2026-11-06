"""Text the owner once when a scheduled job fails, in plain English (no stack traces).

The message says which job, whether money/positions are affected, and a likely cause class (provider did
not respond vs internal error). Technical detail stays in the job log on the volume. At most one text
per job per ET day (marker files in reports/ops/alerts/), so a repeating failure cannot flood the phone.

Uses Twilio SMS when its secrets exist (twilio_account_sid / twilio_auth_token / twilio_from_number /
owner_phone, keychain or SGT_SECRET_*). Without them it says so on stderr and exits 0: the failure is
still recorded in the job log ("!!! exit N"), so nothing is hidden; alerting is simply not configured.

Usage:
    python scripts/ops_notify.py digest --log reports/ops/run.log
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from sgt.core.secrets import MissingSecretError
from sgt.signals_live.notify import TwilioSms

ET = ZoneInfo("America/New_York")
TAIL_LINES = 30
ALERTS = Path("reports/ops/alerts")
# job -> (what it is, what the failure means for money/positions). 2026-11-03: owner asked for no raw errors.
JOBS = {
    "megacap": ("megacap model update", "No new megacap trade is proposed until it runs again; open positions are unchanged."),
    "etf": ("ETF research record update", "Nothing was traded."),
    "etfbook": ("ETF book proposal", "No ETF change is proposed until it runs again; holdings are unchanged."),
    "rebalance": ("60/40 rebalance", "Reply STATUS before approving anything else."),
    "tom": ("turn-of-month check", "No turn-of-month trade is proposed until it runs again."),
    "digest": ("nightly digest", "Trades and positions are unaffected; only tonight's summary is missing."),
    "fadesettle": ("fade official results", "They are retried on the next run; positions are unaffected."),
    "borrow": ("borrow-fee snapshot", "Trades are unaffected; borrow details may be missing from trade texts."),
    "fadeforward": ("fade forward research record", "Nothing was traded; it catches up on the next run."),
    "fadechallengers": ("fade challenger shadow scoring", "Nothing was traded; it catches up on the next run."),
    "fadeexits": ("fade exit shadow scoring", "Nothing was traded; it catches up on the next run."),
    "fadehold": ("fade longer-hold shadow scoring", "Nothing was traded; it catches up on the next run."),
    "fadebrain": ("fade sizing-model shadow scoring", "Nothing was traded; it catches up on the next run."),
    "fadetod": ("fade time-of-day sizing shadow", "Nothing was traded; it catches up on the next run."),
    "learning": ("learning scoreboard", "Nothing was traded; the scoreboard shows the previous night."),
    "charts": ("chart page refresh", "Trades are unaffected; the chart page shows the previous version."),
    "summary": ("midnight day summary", "Trades and positions are unaffected; only tonight's summary is missing."),
    "pilotbook": ("real-money 60/40 / turn-of-month at Schwab",
                  "No real order went out in this run; the next run (5 min) retries. Exits held at Schwab are unaffected."),
}
_NETWORK = re.compile(r"HTTPError|HTTPStatusError|ConnectError|ConnectTimeout|ReadTimeout|TimeoutException|"
                      r"RemoteProtocolError|HTTP[ /]?(429|5\d\d)\b|Too Many Requests|Server Error|Service Unavailable|"
                      r"Temporary failure|Name or service not known", re.I)


_SAFETY = re.compile(r"refusing to trade|stale prices|fail(?:s|ed)? closed|differ from the approved plan", re.I)


def cause(log: Path | None) -> str:
    if not (log and log.exists()):
        return "the cause is in the job log"
    with log.open(errors="replace") as fh:
        tail = "\n".join(deque(fh, maxlen=TAIL_LINES))
    if _NETWORK.search(tail):
        return "a data or SMS provider did not respond (usually clears on the next run)"
    if _SAFETY.search(tail):
        return "a safety check stopped it (prices too old or a plan mismatch), so nothing was traded; it retries next run"
    return "an internal error (logged for a fix)"


def build_message(job: str, log: Path | None, now: datetime | None = None) -> str:
    now = (now or datetime.now(ET)).astimezone(ET)
    what, impact = JOBS.get(job, ("a scheduled job", "Reply STATUS to check the books."))
    return (f"[SGT] Heads-up: the {what} did not finish ({now:%a %H:%M} ET): {cause(log)}. {impact} "
            "No action needed; text PAUSE if you want to stop all trading.")


def first_today(job: str, now: datetime | None = None, root: Path = ALERTS) -> bool:
    """True once per job per ET day (creates the marker); later failures that day are log-only."""
    now = (now or datetime.now(ET)).astimezone(ET)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / f"{re.sub(r'[^a-z0-9_-]', '', job.lower())[:32] or 'job'}-{now:%Y%m%d}"
    try:
        marker.touch(exist_ok=False)
    except FileExistsError:
        return False
    return True


async def _send(text: str) -> None:
    sms = TwilioSms()
    try:
        await sms.send(text)
    finally:
        await sms._c.aclose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("job")
    ap.add_argument("--log", type=Path, default=None)
    args = ap.parse_args(argv)
    text = build_message(args.job, args.log)
    if not first_today(args.job):
        print(f"ops_notify: {args.job} already alerted today; not texting again")
        return 0
    try:
        asyncio.run(_send(text))
    except MissingSecretError:
        print("ops_notify: Twilio not configured; alert not sent: " + text, file=sys.stderr)
        return 0
    print("ops_notify: sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
