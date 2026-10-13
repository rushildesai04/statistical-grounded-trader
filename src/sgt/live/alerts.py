"""Operational alerting for the live runtime (plan §11, §14).

Wraps a :class:`~sgt.brokers.alert_only.Notifier` (``LogNotifier`` /
``WebhookNotifier`` are reused unchanged) and adds dedupe: an identical
``(title, body)`` alert is suppressed if it was already sent within
``window_s`` (default 300s / 5 min). This keeps a flapping condition (a stale
feed, a repeated reject) from drowning the operator while still re-alerting once
the window lapses.

The notifier call is bounded: a notifier that raises (e.g. a webhook timeout
surfaced as ``BrokerUnavailableError``) is caught, logged and counted — an alert
failing to send must never crash the trading loop, but it is never silently
dropped either.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sgt.brokers.alert_only import Notifier

log = logging.getLogger(__name__)

_DEFAULT_WINDOW_S = 300.0


class AlertManager:
    """Rate-limited operational notifier. Not thread-safe: the live loop is single
    task; the background pollers route alerts through the loop, not concurrently."""

    def __init__(
        self,
        notifier: Notifier,
        *,
        window_s: float = _DEFAULT_WINDOW_S,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        if window_s < 0:
            raise ValueError("window_s must be >= 0")
        self._notifier = notifier
        self._window = timedelta(seconds=window_s)
        self._clock = clock
        self._last_sent: dict[tuple[str, str], datetime] = {}
        self.sent_count = 0
        self.suppressed_count = 0
        self.failed_count = 0

    def alert(self, title: str, body: str) -> bool:
        """Send an operational alert, deduped within the window. Returns True if it
        was actually dispatched, False if suppressed as a recent duplicate."""
        now = self._clock()
        key = (title, body)
        last = self._last_sent.get(key)
        if last is not None and (now - last) < self._window:
            self.suppressed_count += 1
            return False
        self._last_sent[key] = now
        try:
            self._notifier.send(title, body)
            self.sent_count += 1
            return True
        except Exception as exc:  # bounded: an alert failure must not crash the loop
            self.failed_count += 1
            log.warning("alert dispatch failed (%s): %s", type(exc).__name__, title)
            return False
