"""Schwab pilot wiring for long-running processes (fade listener) and cron jobs.

Off unless ``SGT_PILOT_SCHWAB=1``. Connection failures never stop the paper book: the owner is texted once per
day, the handle retries every RETRY, and real orders simply do not happen until Schwab is reachable again.
The account is chosen by the secret ``schwab_account_last4`` (never logged or texted).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from sgt.ops.sms_style import bold
from sgt.ops.store import OpsStore, utcnow
from sgt.pilot.executor import Pilot

log = logging.getLogger(__name__)

ENV = "SGT_PILOT_SCHWAB"
RETRY = timedelta(minutes=10)
LOGIN_WARN = timedelta(hours=36)


def enabled() -> bool:
    return os.environ.get(ENV) == "1"


def _once(db: OpsStore, key: str, send, text: str) -> None:
    from sgt.pilot.fade_real import _once as once
    once(db, key, send, text)


class PilotHandle:
    def __init__(self, db: OpsStore, send) -> None:
        self.db, self.send = db, send
        self.pilot: Pilot | None = None
        self._next_try: datetime | None = None

    async def get(self, now: datetime | None = None) -> Pilot | None:
        if not enabled():
            return None
        now = now or utcnow()
        if self.pilot is not None:
            return self.pilot
        if self._next_try and now < self._next_try:
            return None
        self._next_try = now + RETRY
        from sgt.brokers.schwab import SchwabBroker
        from sgt.core.secrets import get_secret
        broker = None
        try:
            last4 = str(get_secret("schwab_account_last4"))
            if not (len(last4) == 4 and last4.isdigit()):
                raise ValueError("schwab_account_last4 must be 4 digits")
            broker = SchwabBroker("schwab_live", last4)
            await broker.connect()
        except Exception as e:  # noqa: BLE001 - real trading stays off; owner is told in plain words, paper continues
            log.error("schwab pilot connect failed: %s", type(e).__name__)
            if broker is not None:
                await broker.close()
            _once(self.db, f"pilot_connect_fail:{now.date()}", self.send,
                  f"[SGT] ⚠️ {bold('Schwab not connected')} ({type(e).__name__}): no REAL orders until it is. "
                  "Paper book continues. If the login expired, run schwab_auth.py --push-fly on the Mac.")
            return None
        self.pilot = Pilot(self.db, broker)
        log.info("schwab pilot connected")
        return self.pilot

    async def login_check(self, now: datetime | None = None) -> None:
        """Day-6 reminder: Schwab's refresh token lasts ~7 days; texts once a day when under 36 h remain."""
        if self.pilot is None:
            return
        now = now or utcnow()
        h = await self.pilot.broker.health()        # type: ignore[attr-defined]
        left = h.auth_expires_at - now if h.auth_expires_at else None
        if left is None or left < LOGIN_WARN:
            when = "now" if left is None or left.total_seconds() <= 0 else f"within {left.total_seconds() / 3600:.0f} h"
            _once(self.db, f"pilot_login_warn:{now.date()}", self.send,
                  f"[SGT] 🔑 {bold('Schwab login expires')} {when}. On the Mac: "
                  "python scripts/schwab_auth.py --push-fly (2 minutes). Without it REAL orders stop.")

    async def close(self) -> None:
        if self.pilot is not None:
            await self.pilot.broker.close()          # type: ignore[attr-defined]
            self.pilot = None
