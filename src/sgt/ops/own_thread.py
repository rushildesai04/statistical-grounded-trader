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

Where the record is kept (2026-11-27). One file per UTC day next to the ops database, ``loop_gaps.<day>.jsonl``.
Until then it was one file, ``loop_gaps.jsonl``, that stopped taking rows for good at 1 MB (it held 194 KB after
four days: full about 2026-12-14, and the nightly check of the feed recorder reads this record). The old file is
read and never written or removed. A session's regular hours lie inside one UTC day in both halves of the year
(13:30-20:00 or 14:30-21:00 UTC), so no evaluation window is split across files and a day's file is never cut
while its session runs.

Bounds. A normal day is about 48 KB. Late-pass and restart rows stop at GAPS_MAX_BYTES (1 MB) for the day; the
10-minute interval rows, which the evaluation needs and of which a day has at most a few hundred, go on to twice
that. Both stops are said in the nightly line. Day files older than GAPS_KEEP_DAYS (60) are removed by ``prune``,
which the nightly line calls from its own process: no thread of a book ever lists, reads or deletes. So the record
is at most 61 day files: about 3 MB as measured, 122 MB if every day hit both stops. A write is one ``stat`` and
one append of one line, as before; nothing is read or rewritten on the write path except the last byte of the
day's file once per process (a line torn by a crash or a full disk must not swallow the row after it).
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
GAPS_NAME = "loop_gaps.jsonl"               # the one file used until 2026-11-27: read, never written or removed
GAPS_MAX_BYTES, GAPS_MAX_LINES = 1_000_000, 10_000      # per day file (it was: for the one file, for good)
GAPS_KEEP_DAYS = 60                         # day files older than this are removed by ``prune``
_DAY_FILE = re.compile(r"loop_gaps\.(\d{4}-\d{2}-\d{2})\.jsonl")
_tail_seen: str | None = None               # the day file whose last byte this process knows to be a line end
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


def part(folder: Path, day: str) -> Path:
    """The record's file for one UTC day (ISO date)."""
    return Path(folder) / f"loop_gaps.{day}.jsonl"


def files(folder: Path, days) -> list[Path]:
    """Where the rows of these UTC days can be, in the order they were written: the old single file, then each
    day's own file. A file that does not exist is simply not there."""
    return [Path(folder) / GAPS_NAME] + [part(folder, d) for d in sorted({str(d) for d in days})]


def capped(folder: Path, day: str, hard: bool = False) -> bool:
    """Whether the day's file has reached the size at which late-pass rows are no longer written (``hard``: the size
    at which no row is written any more, the interval rows included)."""
    try:
        return part(folder, day).stat().st_size > GAPS_MAX_BYTES * (2 if hard else 1)
    except OSError:
        return False


def _write(folder: Path, row: dict) -> None:
    """Append one row to its day's file. One ``stat`` and one small append; never raises, never waits on a lock.
    Late-pass rows stop at GAPS_MAX_BYTES for the day and interval rows at twice that (see the module's text)."""
    global _tail_seen
    key = None
    try:
        day = str(row.get("at", ""))[:10]
        if not _DAY_FILE.fullmatch(f"loop_gaps.{day}.jsonl"):
            day = datetime.now(timezone.utc).date().isoformat()
        p = part(folder, day)
        try:
            size = p.stat().st_size
        except FileNotFoundError:
            size = 0
        if size > GAPS_MAX_BYTES * (2 if row.get("kind") == "summary" else 1):
            return
        line, key = json.dumps(row) + "\n", str(p)
        if size and _tail_seen != key:                  # once per process and day: is the last line whole?
            with p.open("rb") as f:
                f.seek(-1, 2)
                if f.read(1) != b"\n":
                    line = "\n" + line                  # a torn line stays one unreadable line; this row is its own
        with p.open("a", encoding="utf-8") as f:
            f.write(line)
        _tail_seen = key
    except Exception:  # noqa: BLE001 - a measurement: it must never break the pass it measures
        if key is not None and _tail_seen == key:
            _tail_seen = None                           # e.g. a full disk tore this line: look again before the next
        log.exception("late pass not recorded")


def unevaluated(folder: Path):
    """``day -> bool``: True when the day is an exchange session that the nightly recorder check
    (``scripts/feed_shadow.py``, one line a session in ``reports/feed_shadow/daily.jsonl``) has no line for. Such a
    day's file is never pruned: its rows have not been held against the tolerance. Where that check has never
    written a line at all it is not in use and no day is waiting for it. Anything that cannot be decided (the file
    cannot be read, the calendar cannot be asked) counts as unevaluated: kept."""
    lines = Path(folder).resolve().parent / "feed_shadow" / "daily.jsonl"
    try:
        if not lines.exists():
            return lambda day: False
        seen = set()
        for ln in lines.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                r = json.loads(ln)
            except ValueError:
                continue
            if isinstance(r, dict) and r.get("session"):
                seen.add(str(r["session"]))
        from sgt.intraday.calendar import default_calendar

        cal = default_calendar()
    except Exception:  # noqa: BLE001 - undecided: keep everything
        log.exception("stop-check record: cannot tell which sessions were evaluated; nothing is removed")
        return lambda day: True

    def waiting(day: str) -> bool:
        try:
            return day not in seen and bool(cal.is_session(date.fromisoformat(day)))
        except Exception:  # noqa: BLE001 - undecided: keep
            return True
    return waiting


def prune(folder: Path, today: date | None = None, keep_days: int = GAPS_KEEP_DAYS, waiting=None,
          kept: list[str] | None = None) -> list[str]:
    """Remove the day files older than ``keep_days`` and return their names. Only files named exactly like a day
    file with a real date: never the old single file, never today's or a later day's, nothing else in the folder
    and nothing in any other folder (the recorder's lines, the evidence kept for a breach and the research data
    live elsewhere and are never listed here). A day ``waiting(day)`` says is an unevaluated session is kept
    whatever its age, and named in ``kept``. Called by the nightly health line, in its own process. Never raises."""
    gone = []
    try:
        today = today or datetime.now(timezone.utc).date()
        waiting = unevaluated(folder) if waiting is None else waiting
        for p in sorted(Path(folder).glob("loop_gaps.*.jsonl")):
            m = _DAY_FILE.fullmatch(p.name)
            try:
                old = m is not None and (today - date.fromisoformat(m.group(1))).days > keep_days
            except ValueError:
                continue
            if old and waiting(m.group(1)):
                if kept is not None:
                    kept.append(p.name)
                continue
            if old:
                try:
                    p.unlink()
                    gone.append(p.name)
                except OSError:                         # one file that cannot go does not keep the others
                    log.exception("an old day file of the stop-check record could not be removed: %s", p.name)
    except Exception:  # noqa: BLE001 - housekeeping: said in the log, never raised into the summary
        log.exception("old day files of the stop-check record could not be removed")
    if gone:
        log.warning("stop-check record: removed %d day files older than %d days: %s", len(gone), keep_days, ", ".join(gone))
    return gone


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
    """The late passes and restarts written down on one UTC day (ISO date): from the old single file and from the
    day's own file, in the order they were written."""
    out = []
    try:
        for p in files(folder, [day]):
            for raw in (p.read_text(encoding="utf-8", errors="replace").splitlines() if p.exists() else [])[-GAPS_MAX_LINES:]:
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
        if capped(folder, day):                             # a full day stops taking rows: say so, never silently
            bad.append("the record of stop checks is full for this day (later late checks are not in it)")
        held: list[str] = []
        gone = prune(folder, kept=held)                     # this process, at night: never a book's thread
        kept = (f"; {len(gone)} day files of the record older than {GAPS_KEEP_DAYS} days removed" if gone else "") + (
            f"; {len(held)} older day files kept: their sessions have no recorder check yet" if held else "")
        return (("⚠️ stop checks: " + measured + "; ".join(bad)) if bad else
                f"stop checks: {measured}none late, no restart in alert hours") + kept
    except Exception as e:  # noqa: BLE001 - the summary must go out
        log.exception("stop checks clause failed")
        return f"⚠️ stop checks: check failed ({type(e).__name__})"
