"""A loop's own worker thread, and a record of stop checks that started late (operational risk review, 2026-11-23).

Why a thread of its own. The listener runs its loops on one asyncio event loop and hands each pass to the default
worker pool (``asyncio.to_thread``). That pool is small (CPUs + 4: six workers on the runner) and shared with the
research loops, whose passes make slow network calls. With one pass per loop at a time there are exactly as many
users as workers, so the short book's stop check never had to wait for a worker; one more user and it could. The
two books' passes now run on threads nobody else can take, so that no later change can make a stop check queue.

Why a record. "The stop check runs every 10 seconds" was an assumption nothing measured. ``Pace`` writes down every
pass that started later than it should have (whatever the cause: a slow pass before it, the event loop held up, a
busy database), and every start of the listener after a silence; the nightly health line reports them
(``clause``). Nothing here is on a decision path, and nothing here raises.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
GAPS_NAME = "loop_gaps.jsonl"               # next to the ops database
GAPS_MAX_BYTES, GAPS_MAX_LINES = 1_000_000, 10_000
RESTART = "listener restart"
DOWN_MIN_S = 60.0                           # a start after a heartbeat silence this long is written down


class OwnThread:
    """One worker thread that only this loop uses."""

    def __init__(self, name: str) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)

    async def run(self, fn, *args, **kw):
        return await asyncio.get_running_loop().run_in_executor(self._pool, functools.partial(fn, *args, **kw))


def late_after(every: float) -> float:
    """A pass that starts more than this long after the one before it is late: two intervals and 5 s (25 s for the
    10-second stop check: at least one whole check did not happen)."""
    return 2 * every + 5


def _write(folder: Path, row: dict) -> None:
    try:
        p = Path(folder) / GAPS_NAME
        if p.exists() and p.stat().st_size > GAPS_MAX_BYTES:
            return
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:  # noqa: BLE001 - a measurement: it must never break the pass it measures
        log.exception("late pass not recorded")


class Pace:
    """Call ``start()`` at the beginning of every pass of one loop. A start later than ``late_after(every)`` after
    the previous one is written down with the gap; returns that gap (None when on time)."""

    def __init__(self, folder: Path, name: str, every: float, clock=time.monotonic) -> None:
        self.folder, self.name, self.every, self.clock, self.last = Path(folder), name, float(every), clock, None

    def start(self) -> float | None:
        now, last = self.clock(), self.last
        self.last = now
        if last is None or now - last <= late_after(self.every):
            return None
        gap = round(now - last, 1)
        _write(self.folder, {"at": datetime.now(timezone.utc).isoformat(), "loop": self.name, "gap": gap,
                             "every": self.every})
        log.warning("%s: a pass started %.0f s after the one before it (every %.0f s)", self.name, gap, self.every)
        return gap


def note_restart(folder: Path, heartbeat: str | None, now: datetime | None = None) -> float | None:
    """At the listener's start: how long the book's heartbeat had been silent. Written down from DOWN_MIN_S on (a
    deploy is about 30 to 90 s, so deploys show too: the clause only warns about one inside alert hours)."""
    try:
        now = now or datetime.now(timezone.utc)
        down = (now - datetime.fromisoformat(str(heartbeat))).total_seconds()
    except (TypeError, ValueError):
        return None
    if down < DOWN_MIN_S:
        return None
    _write(folder, {"at": now.isoformat(), "loop": RESTART, "gap": round(down, 1), "every": 0})
    return down


def gaps(folder: Path, day: str) -> list[dict]:
    """The late passes and restarts written down on one UTC day (ISO date)."""
    p, out = Path(folder) / GAPS_NAME, []
    try:
        for raw in (p.read_text(encoding="utf-8").splitlines() if p.exists() else [])[-GAPS_MAX_LINES:]:
            try:
                r = json.loads(raw)
            except ValueError:
                continue
            if isinstance(r, dict) and str(r.get("at", "")).startswith(day):
                out.append(r)
    except OSError:
        log.exception("the record of late passes cannot be read")
    return out


def clause(folder: Path, day: str, in_hours=None) -> str:
    """The ``stop checks:`` clause of the nightly health line. ``in_hours(datetime) -> bool``: alert hours of a
    session (a restart inside them is a warning; a deploy at night is not news). Never raises."""
    try:
        rows = gaps(folder, day)
        late = [r for r in rows if r.get("loop") != RESTART]
        down = []
        for r in rows:
            if r.get("loop") == RESTART:
                try:
                    at = datetime.fromisoformat(r["at"])
                except (KeyError, TypeError, ValueError):
                    continue
                if in_hours is None or in_hours(at):
                    down.append((at, float(r.get("gap") or 0)))
        bad = []
        if late:
            w = max(late, key=lambda r: float(r.get("gap") or 0))
            bad.append(f"{len(late)} started late, longest gap {float(w.get('gap') or 0):.0f} s ({w.get('loop')}, "
                       f"due every {float(w.get('every') or 0):.0f} s)")
        if down:
            at, gap = max(down, key=lambda x: x[1])
            bad.append(f"listener restarted {len(down)}x in alert hours, longest silence {gap:.0f} s (at {at:%H:%M} UTC)")
        return ("⚠️ stop checks: " + "; ".join(bad)) if bad else "stop checks: none late, no restart in alert hours"
    except Exception as e:  # noqa: BLE001 - the summary must go out
        log.exception("stop checks clause failed")
        return f"⚠️ stop checks: check failed ({type(e).__name__})"
