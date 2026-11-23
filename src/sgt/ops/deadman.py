"""An outside probe for a machine that is down (prepared 2026-11-23; INACTIVE until an address is set).

Every check this system runs (the watchdog, the 09:20 ET check, the nightly line) runs ON the runner. If the machine
is down, so are they, and nothing tells the owner. The smallest remedy is a dead-man's switch held by a service
outside the machine: the per-minute control job calls an address once a minute; when the calls stop for a few
minutes, the outside service notifies the owner. The runner needs no reachable address of its own.

Nothing is sent unless the secret ``deadman_url`` (env ``SGT_SECRET_DEADMAN_URL`` on the runner) holds an https address.
The call carries no data: a GET of that address, or of address + "/fail" when the listener is silent in alert hours
(so the outside service also reports a hung listener if the text service itself is down). 5 s timeout; never raises.
Options, access and cost: docs/findings/ops_followups_2026-11-23.md, section 2.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
SECRET = "deadman_url"
TIMEOUT_S = 5.0
MAX_LEN = 300


def address() -> str | None:
    """The configured address, or None when the probe is off or the value is not a plain https address."""
    try:
        from sgt.core.secrets import get_secret

        url = (get_secret(SECRET, required=False) or "").strip()
    except Exception:  # noqa: BLE001 - no secret store: the probe is simply off
        return None
    if not url:
        return None
    if not url.startswith("https://") or len(url) > MAX_LEN or any(c.isspace() for c in url):
        log.error("the outside probe's address is not a plain https address: not used")
        return None
    return url.rstrip("/")


def ping(ok: bool = True, get=None) -> str:
    """One call to the outside service. Returns "off" (no address), "sent" or "failed". ``get(url, timeout=...)``
    defaults to httpx.get."""
    url = address()
    if url is None:
        return "off"
    try:
        if get is None:
            import httpx

            get = httpx.get
        get(url if ok else url + "/fail", timeout=TIMEOUT_S)
        return "sent"
    except Exception as exc:  # noqa: BLE001 - a missed call is what the outside service is there to notice
        log.warning("outside probe call failed: %s", type(exc).__name__)
        return "failed"
