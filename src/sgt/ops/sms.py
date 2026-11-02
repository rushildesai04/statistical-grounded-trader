"""Twilio SMS for the runner: send to the owner, and poll the owner's replies (no public webhook).

Only messages whose sender is exactly the configured ``owner_phone`` are returned; replies are only
ever sent to ``owner_phone``. Secrets: twilio_account_sid, twilio_auth_token, twilio_from_number,
owner_phone (keychain or SGT_SECRET_*). Bodies are untrusted: truncated here, parsed strictly by
``sgt.ops.approvals.parse``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

from sgt.core.secrets import get_secret

log = logging.getLogger(__name__)
SMS_MAX = 1500
BODY_MAX = 200
TIMEOUT_S = 10.0


@dataclass(frozen=True, slots=True)
class Inbound:
    sid: str
    body: str
    sent_at: datetime


class Sms:
    def __init__(self, client: httpx.Client | None = None) -> None:
        sid = get_secret("twilio_account_sid")
        self.from_number = get_secret("twilio_from_number")
        self.owner = get_secret("owner_phone")
        self._c = client or httpx.Client(base_url=f"https://api.twilio.com/2010-04-01/Accounts/{sid}",
                                         auth=(sid, get_secret("twilio_auth_token")), timeout=TIMEOUT_S)

    def send(self, text: str) -> None:
        r = self._c.post("/Messages.json", data={"From": self.from_number, "To": self.owner, "Body": text[:SMS_MAX]})
        if r.status_code >= 400:
            raise RuntimeError(f"twilio send HTTP {r.status_code}")

    def inbound(self, since: datetime) -> list[Inbound]:
        """Owner -> runner messages dated on/after ``since``'s UTC day, oldest first."""
        r = self._c.get("/Messages.json", params={"To": self.from_number, "From": self.owner,
                                                  "DateSent>": since.strftime("%Y-%m-%d"), "PageSize": 50})
        if r.status_code >= 400:
            raise RuntimeError(f"twilio list HTTP {r.status_code}")
        out = []
        for m in r.json().get("messages", []) or []:
            if not isinstance(m, dict) or m.get("direction") != "inbound" or m.get("from") != self.owner:
                continue
            sent = m.get("date_sent") or m.get("date_created")
            sid = str(m.get("sid") or "")
            if not sent or not sid.startswith(("SM", "MM")):
                continue
            try:
                ts = parsedate_to_datetime(sent)
            except (TypeError, ValueError):
                log.warning("unparseable twilio date on %s", sid[:64])
                continue
            ts = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)      # RFC 2822 "-0000" -> naive
            out.append(Inbound(sid[:64], str(m.get("body") or "")[:BODY_MAX], ts))
        return sorted(out, key=lambda m: m.sent_at)

    def close(self) -> None:
        self._c.close()


def notify(text: str, sms: Sms | None = None) -> bool:
    """Best-effort owner SMS for informational messages; failures are logged, never raised."""
    own = sms is None
    try:
        sms = sms or Sms()
        sms.send(text)
        return True
    except Exception as exc:  # noqa: BLE001 - informational path; caller's job must not fail on SMS
        log.error("sms notify failed: %s", type(exc).__name__)
        return False
    finally:
        if own and sms is not None:
            sms.close()
