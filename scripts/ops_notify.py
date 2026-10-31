"""Send one operational alert (job failure) to the owner's phone.

Uses Twilio SMS when its secrets exist (twilio_account_sid / twilio_auth_token / twilio_from_number /
owner_phone, keychain or SGT_SECRET_*). Without them it says so on stderr and exits 0: the failure is
still recorded in the job log ("!!! exit N"), so nothing is hidden; alerting is simply not configured.

Usage:
    python scripts/ops_notify.py "megacap job failed (exit 1)" [--log reports/megacap_forward/run.log]
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from collections import deque
from pathlib import Path

from sgt.core.secrets import MissingSecretError
from sgt.signals_live.notify import TwilioSms

TAIL_LINES = 3


def build_message(title: str, log: Path | None) -> str:
    text = f"[sgt-runner] {title}"
    if log and log.exists():
        with log.open(errors="replace") as fh:
            tail = [ln.strip() for ln in deque(fh, maxlen=TAIL_LINES) if ln.strip()]
        if tail:
            text += "\n" + "\n".join(t[:150] for t in tail)
    return text


async def _send(text: str) -> None:
    sms = TwilioSms()
    try:
        await sms.send(text)
    finally:
        await sms._c.aclose()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("title")
    ap.add_argument("--log", type=Path, default=None)
    args = ap.parse_args(argv)
    text = build_message(args.title, args.log)
    try:
        asyncio.run(_send(text))
    except MissingSecretError:
        print("ops_notify: Twilio not configured; alert not sent: " + text.splitlines()[0], file=sys.stderr)
        return 0
    print("ops_notify: sent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
