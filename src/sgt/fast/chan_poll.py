"""Fast check of the alert channel (owner 2026-11-18: "move the watcher from every 60 seconds to every 30 or 15 secs").

Why: Telegram does not push this channel's posts to our client. The library asks for them on the server's timer,
about every 60 seconds, so over all 362 alerts received from 2026-11-04 to 11-18 the alert reached the book a median
27.7 s after it was posted (90th percentile 52.9 s; within 5 s: 9%). This asks the channel for anything newer than
the last message seen every EVERY seconds during the session, and hands each new message to the same handler the
push path uses. The push path stays: whichever sees a message first handles it, the other finds it in ``Seen``.

2026-11-22 (listener recovery): the live listener no longer calls ``poll_once`` or ``pushed`` from this module.
The same check now runs from a DURABLE cursor and also replays what was posted while the process was down
(``sgt.fast.alert_recovery.ordered_pass`` / ``pushed``). ``Seen``, ``interval``, ``active`` and ``flood_pause`` are
still used; ``State``, ``pushed`` and ``poll_once`` are kept as the record of the in-memory design they replaced
(first pass = baseline only, so an alert posted during a restart was never handled: VRAX, 2026-11-04).

Bounded and fail-safe: one small request per pass (MAX_BATCH messages at most); the first pass only sets the
baseline (nothing older than the start is replayed); a Telegram "slow down" answer pauses the check for as long
as it asks (capped); any other failure is logged by the caller and the next pass simply tries again. Flag
``chan_poll_s`` in the ops store changes the interval without a deploy ("0" = off; 5 to 120 otherwise).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, time

from sgt.fast.fade import ET
from sgt.intraday.calendar import default_calendar

EVERY = 15                               # seconds between checks during the session
FLAG = "chan_poll_s"
MIN_S, MAX_S = 5, 120
WINDOW = (time(9, 25), time(16, 0))      # ET: alerts outside the session are filtered anyway and arrive by push
MAX_BATCH = 50
SEEN_MAX = 5_000
FLOOD_CAP_S = 900


class Seen:
    """Message ids already handled (by push or by poll), bounded."""

    def __init__(self, cap: int = SEEN_MAX) -> None:
        self._order: deque[int] = deque(maxlen=cap)
        self._ids: set[int] = set()

    def add(self, mid: int) -> bool:
        """True when ``mid`` is new (and now remembered); False when it was already handled."""
        if mid in self._ids:
            return False
        if len(self._order) == self._order.maxlen:
            self._ids.discard(self._order[0])
        self._order.append(mid)
        self._ids.add(mid)
        return True


@dataclass
class State:
    last_id: int | None = None
    seen: Seen = field(default_factory=Seen)


def interval(value: str | None) -> int:
    """Seconds between checks from the flag: 0 = off; unset or unreadable = EVERY; clamped to MIN_S..MAX_S."""
    if value is None or not value.strip():
        return EVERY
    try:
        v = int(value)
    except ValueError:
        return EVERY
    return 0 if v == 0 else max(MIN_S, min(MAX_S, v))


def active(now: datetime) -> bool:
    """During a trading session's alert hours (ET)."""
    et = now.astimezone(ET)
    return default_calendar().is_session(et.date()) and WINDOW[0] <= et.time() < WINDOW[1]


def flood_pause(seconds) -> int:
    """How long to stop asking after Telegram says to slow down: what it asks plus a second, capped."""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        s = 60
    return min(max(s, 1), FLOOD_CAP_S) + 1


def pushed(state: State, dispatch):
    """The handler for the PUSH path: ``take(message)`` passes a message to ``dispatch(message, "push")`` unless the
    poll (or an earlier push) already handled it. Returns whether it was dispatched.

    The poll does its own check in ``poll_once`` and must be given ``dispatch`` itself, never this guard: a message
    goes through exactly ONE "seen" check. Until 2026-11-19 both paths shared one guarded handler, so a message the
    poll found first was marked seen by ``poll_once`` and then refused by the handler as already seen: dropped, and
    the later push was dropped too (adversarial review 2026-11-19; no alert was lost in the two sessions it ran: the
    pushed update had always arrived first)."""
    def take(message) -> bool:
        if not state.seen.add(message.id):
            return False
        dispatch(message, "push")
        return True
    return take


async def poll_once(get_messages, state: State, handle) -> int:
    """One check. ``get_messages(**kw)`` -> messages (each with an int ``id``); ``handle(message)`` is called once per
    NEW message, oldest first. Returns how many it handled. The first call only records the newest id.
    ``handle`` must do the work itself and must NOT check ``state.seen`` again (see ``pushed``)."""
    if state.last_id is None:
        ids = [m.id for m in await get_messages(limit=1) if isinstance(getattr(m, "id", None), int)]
        state.last_id = max(ids, default=0)
        return 0
    got = await get_messages(min_id=state.last_id, limit=MAX_BATCH)
    new = sorted((m for m in list(got)[:MAX_BATCH] if isinstance(getattr(m, "id", None), int) and m.id > state.last_id),
                 key=lambda m: m.id)
    n = 0
    for m in new:
        state.last_id = m.id
        if state.seen.add(m.id):
            handle(m)
            n += 1
    return n
