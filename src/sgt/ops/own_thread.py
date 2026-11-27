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
EVENT_LOOP = "event loop"
DOWN_MIN_S = 60.0                           # a start after a heartbeat silence this long is written down
FLUSH_S = 600.0                             # the measured intervals of a loop are written down this often


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


def beat(folder: Path, name: str) -> None:
    """Touch a loop's liveness file (no database: a heartbeat must not queue for the write lock). Read by the
    watchdog, a separate process. Never raises."""
    try:
        p = Path(folder) / f"beat.{name.replace(' ', '_')}"
        p.touch()
    except Exception:  # noqa: BLE001 - a heartbeat that cannot be written is the watchdog's finding, not a crash
        log.exception("heartbeat of %s not written", name)


def beat_age(folder: Path, name: str, now: float | None = None) -> float | None:
    """Seconds since ``name`` last beat; None when it never did (or the file cannot be read)."""
    try:
        return (time.time() if now is None else now) - (Path(folder) / f"beat.{name.replace(' ', '_')}").stat().st_mtime
    except OSError:
        return None


class Pace:
    """The OBSERVED timing of one loop's stop checks. ``start()`` at the beginning of every check: beats, measures
    the time since the check before it, and writes it down at once when it is later than ``late_after(every)``
    (returns that gap, else None). ``finish()`` at its end measures how long the check itself took. Every FLUSH_S
    the intervals of the period are written down as counts (within 1.1 x the intended interval, within 1.5 x,
    within the late limit, later), with the longest interval and the longest single check, so the nightly line can
    say what the cadence WAS instead of what it was meant to be."""

    def __init__(self, folder: Path, name: str, every: float, clock=time.monotonic) -> None:
        self.folder, self.name, self.every, self.clock, self.last = Path(folder), name, float(every), clock, None
        self._t0, self._flushed = None, None
        self._reset()

    def _reset(self) -> None:
        self.counts, self.max_gap, self.max_work, self.n = [0, 0, 0, 0], 0.0, 0.0, 0

    def start(self) -> float | None:
        now, last = self.clock(), self.last
        self.last = self._t0 = now
        beat(self.folder, self.name)
        if last is None:
            return None
        gap = now - last
        e = self.every
        self.counts[0 if gap <= 1.1 * e else 1 if gap <= 1.5 * e else 2 if gap <= late_after(e) else 3] += 1
        self.n, self.max_gap = self.n + 1, max(self.max_gap, gap)
        if self._flushed is None:
            self._flushed = now
        elif now - self._flushed >= FLUSH_S:
            self.flush()
        if gap <= late_after(e):
            return None
        gap = round(gap, 1)
        _write(self.folder, {"at": datetime.now(timezone.utc).isoformat(), "loop": self.name, "gap": gap,
                             "every": self.every})
        log.warning("%s: a pass started %.0f s after the one before it (every %.0f s)", self.name, gap, self.every)
        return gap

    def finish(self) -> None:
        if self._t0 is not None:
            self.max_work = max(self.max_work, self.clock() - self._t0)

    def sweep(self):
        """For a book's ``on_sweep`` hook: start now, and hand back what to call when the check is done."""
        self.start()
        return self.finish

    def flush(self) -> None:
        if self.n:
            _write(self.folder, {"at": datetime.now(timezone.utc).isoformat(), "loop": self.name, "kind": "summary",
                                 "n": self.n, "counts": self.counts, "max_gap": round(self.max_gap, 1),
                                 "max_work": round(self.max_work, 1), "every": self.every})
        self._flushed = self.clock()
        self._reset()


async def watch_loop(folder: Path, every: float = 1.0, late: float = 3.0, clock=time.monotonic) -> None:
    """A task that should wake every ``every`` seconds. A wake more than ``late`` seconds after the one before it
    means the event loop stood still (something did blocking work on it): written down, whatever the cause."""
    last = clock()
    while True:
        await asyncio.sleep(every)
        now = clock()
        gap, last = now - last, now
        if gap > late:
            log.warning("the event loop stood still for %.1f s", gap)
            await asyncio.to_thread(_write, Path(folder), {"at": datetime.now(timezone.utc).isoformat(),
                                                           "loop": EVENT_LOOP, "gap": round(gap, 1), "every": every})


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


# 2026-11-25 (BNAI: the long book "checked" every 15 s and still saw the stop 2 min 47 s after the tape). A check of
# the long book reads the free feed's CLOSED one-minute candles, so how often it looks says nothing about how soon it
# sees a stop: on 15 past stop exits the candle that showed it closed a median 39 s and up to 22 minutes after the
# tape's first print there (docs/findings/long_stop_detection_2026-11-25.md). The line says so; the short book's
# figures are never evidence for the long book's exits.
# 2026-11-25 (afternoon): the look now also reads the free feed's last trade for a print at the stop
# (long_book.between_candles). Still one thin feed: the wording says what is looked at, and that it is not protection.
LOOKS = {"long book": " (how often it LOOKS, at closed free-feed candles and at the free feed's last trade; that feed "
                      "shows a long's stop later than the tape, or not at all: not stop protection)"}


def _measured(rows: list[dict], in_hours) -> str:
    """What the intervals between stop checks WERE in alert hours, per loop ("" when nothing was measured)."""
    out = []
    for name in sorted({r.get("loop") for r in rows if r.get("kind") == "summary"}):
        n, counts, gap, work, every = 0, [0, 0, 0, 0], 0.0, 0.0, 0.0
        for r in rows:
            if r.get("kind") != "summary" or r.get("loop") != name:
                continue
            try:
                if in_hours is not None and not in_hours(datetime.fromisoformat(r["at"])):
                    continue
                c = [int(x) for x in r["counts"]]
                n, counts = n + int(r["n"]), [a + b for a, b in zip(counts, c)]
                gap, work, every = max(gap, float(r["max_gap"])), max(work, float(r["max_work"])), float(r["every"])
            except (KeyError, TypeError, ValueError):
                continue
        if n:
            out.append(f"{name} {n:,} checks, {counts[0] / n:.1%} within {1.1 * every:.0f} s, longest gap {gap:.0f} s, "
                       f"longest single check {work:.0f} s" + LOOKS.get(str(name), ""))
    return ("measured in alert hours: " + "; ".join(out) + "; ") if out else ""


def clause(folder: Path, day: str, in_hours=None) -> str:
    """The ``stop checks:`` clause of the nightly health line. ``in_hours(datetime) -> bool``: alert hours of a
    session (a restart inside them is a warning; a deploy at night is not news). Never raises."""
    try:
        rows = gaps(folder, day)
        measured = _measured(rows, in_hours)
        rows = [r for r in rows if r.get("kind") != "summary"]
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
        try:                                                # a full record stops taking rows: say so, never silently
            if (Path(folder) / GAPS_NAME).stat().st_size > GAPS_MAX_BYTES:
                bad.append("the record of stop checks is full (later checks are not in it)")
        except OSError:
            pass
        return ("⚠️ stop checks: " + measured + "; ".join(bad)) if bad else \
            f"stop checks: {measured}none late, no restart in alert hours"
    except Exception as e:  # noqa: BLE001 - the summary must go out
        log.exception("stop checks clause failed")
        return f"⚠️ stop checks: check failed ({type(e).__name__})"
