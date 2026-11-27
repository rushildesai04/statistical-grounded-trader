"""Feed shadow, Stage 0 (owner 2026-11-27: "You are authorized to build the proposed no-cost Stage 0 recorder and
validate it on available delayed data, provided it is isolated from the baseline, uses existing access, and does not
interfere with production. No paid subscription or account change is authorized.";
docs/findings/proposal_realtime_feed_shadow_2026-11-27.md).

What it is. A passive record of what the live long book's candle reader returned at each look: which one-minute
candles it held, and when it first held each one closed. A nightly job (``scripts/feed_shadow.py compare``) sets
that record beside the vendor's finished candles for the same minutes, on the free feed and on the full tape (free
once 15 minutes old), so three things that all look like "no candle" during the session can be told apart:

- nothing traded on the tape in that minute (a genuine no-trade minute, as far as the finished record shows);
- it traded on the tape but not on the free feed's one exchange (the free feed could never have had a candle);
- the free feed's finished record has a candle the live reader was never given (missing data).

What it is not, and how that is kept true:
- it makes NO request in session hours: it only looks at frames the book already fetched (``tap``);
- it never changes, delays or replaces what the reader returns: the wrapped reader's frame is handed back as is, and
  everything this module does around it is inside a guard that swallows its own errors;
- it never waits: a look is put on a bounded queue with ``put_nowait``; when the queue is full the look is counted
  as dropped and forgotten;
- it writes to its OWN database file from its OWN thread, never the operations database, so it can hold no lock the
  books wait for;
- it is off unless ``SGT_FEED_SHADOW=1``; off, ``tap`` returns the reader untouched (not even a wrapper);
- it can be switched off while the listener runs, with no restart and nothing done to the books (owner 2026-11-27:
  "demonstrated blocking or resource exhaustion needs a safe, prompt recorder shutoff without restarting or delaying
  position management"): a file named ``OFF`` beside its database. Its watchdog thread sees the file within
  ``WATCH_S`` seconds and the recorder is off: from then on ``offer`` is one attribute read and a return. While the
  file exists a restarted listener does not start the recorder either;
- it switches ITSELF off, the same way, and writes that file with the reason, so it stays off until a person has
  looked: ``MAX_FAILS`` failed writes in a row; ``SLOW_MAX`` offers that each took over ``SLOW_MS`` in the book's
  thread (it is in the book's way); ``DROP_MAX`` looks dropped (its writer cannot keep up); its database over
  ``MAX_DB_BYTES``; under ``MIN_FREE_BYTES`` free on its volume. These are safety trips, not the performance
  tolerance of the deploy check: that one is judged afterwards from the book's own cadence record;
- no book, text, total, registered calculation or settle reads anything it writes.
"""
from __future__ import annotations

import logging
import math
import os
import queue
import shutil
import sqlite3
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from time import monotonic
from typing import Callable
from zoneinfo import ZoneInfo

import pandas as pd

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
VERSION = "feed-shadow-stage0-v1"
ENV = "SGT_FEED_SHADOW"
DB = Path("reports/feed_shadow/shadow.db")
QUEUE_MAX = 2000            # looks waiting for the writer; the long book makes at most ~60 a minute
TAIL = 12                   # candles of a frame that are recorded per look (the newest ones)
FLUSH_S = 2.0               # the writer commits at least this often
MAX_FAILS = 5               # failed writes in a row before the recorder switches itself off
KEEP_DAYS = 45              # rows older than this are deleted when the recorder starts
OFF_NAME = "OFF"            # a file beside the database: while it exists the recorder is off (and does not start)
WATCH_S = 2.0               # the watchdog looks at that file and at the budgets below this often
SLOW_MS, SLOW_MAX = 250.0, 3        # offers this slow, this many times: the recorder is in the book's way
DROP_MAX = 500                      # looks dropped because the writer cannot keep up
MAX_DB_BYTES = 400_000_000          # its own database with its write-ahead file (the runner's volume is 5.2 GB,
                                    # 2.7 GB free on 2026-11-27; 45 days of looks are expected far below this)
MIN_FREE_BYTES = 500_000_000        # free space left on the volume it writes to
BAR = timedelta(minutes=1)
SYMBOL_MAX = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS looks(id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, session TEXT NOT NULL, at TEXT NOT NULL,
  book_now TEXT, n_bars INTEGER NOT NULL, last_ts TEXT, ms REAL);
CREATE INDEX IF NOT EXISTS looks_by ON looks(session, symbol, at);
CREATE TABLE IF NOT EXISTS bars(symbol TEXT NOT NULL, ts TEXT NOT NULL, session TEXT NOT NULL, first_at TEXT NOT NULL,
  closed_at TEXT, closed_prev_look TEXT, o REAL, h REAL, l REAL, c REAL, v REAL,
  last_at TEXT NOT NULL, lo REAL, lh REAL, ll REAL, lc REAL, lv REAL, revisions INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(symbol, ts));
CREATE INDEX IF NOT EXISTS bars_by ON bars(session, symbol);
CREATE TABLE IF NOT EXISTS health(at TEXT PRIMARY KEY, offered INTEGER, written INTEGER, dropped INTEGER, errors INTEGER,
  fails INTEGER, waiting INTEGER);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
# One candle of one look. ``first_at``: the first look that held it at all (possibly still forming). ``closed_at``:
# the first look at or after its minute ended that held it; ``o..v`` are its values at that look (at ``first_at``
# until then). ``closed_prev_look``: the look of the same symbol before ``closed_at`` (None for the first look of a
# symbol in this process): with it the delivery delay is an interval, not a guess. ``l*``: the latest values held.
UPSERT = """
INSERT INTO bars(symbol, ts, session, first_at, closed_at, closed_prev_look, o, h, l, c, v, last_at, lo, lh, ll, lc, lv)
VALUES (:symbol, :ts, :session, :at, :closed_at, :closed_prev, :o, :h, :l, :c, :v, :at, :o, :h, :l, :c, :v)
ON CONFLICT(symbol, ts) DO UPDATE SET
  o = CASE WHEN closed_at IS NULL AND excluded.closed_at IS NOT NULL THEN excluded.o ELSE o END,
  h = CASE WHEN closed_at IS NULL AND excluded.closed_at IS NOT NULL THEN excluded.h ELSE h END,
  l = CASE WHEN closed_at IS NULL AND excluded.closed_at IS NOT NULL THEN excluded.l ELSE l END,
  c = CASE WHEN closed_at IS NULL AND excluded.closed_at IS NOT NULL THEN excluded.c ELSE c END,
  v = CASE WHEN closed_at IS NULL AND excluded.closed_at IS NOT NULL THEN excluded.v ELSE v END,
  closed_prev_look = CASE WHEN closed_at IS NULL AND excluded.closed_at IS NOT NULL THEN excluded.closed_prev_look
                          ELSE closed_prev_look END,
  revisions = revisions + (CASE WHEN lo IS NOT excluded.lo OR lh IS NOT excluded.lh OR ll IS NOT excluded.ll
                                  OR lc IS NOT excluded.lc OR lv IS NOT excluded.lv THEN 1 ELSE 0 END),
  closed_at = COALESCE(closed_at, excluded.closed_at),
  last_at = excluded.last_at, lo = excluded.lo, lh = excluded.lh, ll = excluded.ll, lc = excluded.lc, lv = excluded.lv
"""
_STOP = object()


def _utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts) -> str:
    return pd.Timestamp(ts).tz_convert("UTC").isoformat()


class Recorder:
    """The queue and its writer thread. Everything the book's thread touches is ``offer`` (see the module doc)."""

    def __init__(self, path: Path = DB, queue_max: int = QUEUE_MAX, clock: Callable[[], datetime] = _utc,
                 watch_s: float = WATCH_S, slow_ms: float = SLOW_MS, slow_max: int = SLOW_MAX,
                 drop_max: int = DROP_MAX, max_db_bytes: int = MAX_DB_BYTES, min_free_bytes: int = MIN_FREE_BYTES,
                 free: Callable[[Path], int] | None = None) -> None:
        self.path, self.clock = Path(path), clock
        self.q: queue.Queue = queue.Queue(maxsize=queue_max)
        self.on, self.why = True, None
        self.offered = self.written = self.dropped = self.errors = self.fails = self.slow = 0
        self.watch_s, self.slow_ms, self.slow_max, self.drop_max = watch_s, slow_ms, slow_max, drop_max
        self.max_db_bytes, self.min_free_bytes = max_db_bytes, min_free_bytes
        self._free = free or (lambda p: shutil.disk_usage(p).free)
        self._thread = threading.Thread(target=self._run, name="feed-shadow", daemon=True)
        self._watcher = threading.Thread(target=self._watch, name="feed-shadow-watch", daemon=True)
        self._halt = threading.Event()
        self._warned = self._said = False

    @property
    def off_file(self) -> Path:
        return self.path.parent / OFF_NAME

    def _trip(self, why: str) -> None:
        """Switch off from any thread: two assignments, no input or output (the book's thread may be the caller).
        The watchdog says it in the log and writes the OFF file."""
        if self.on:
            self.why = why
            self.on = False

    # ---- the book's thread ----
    def offer(self, symbol: str, book_now, frame, ms: float) -> None:
        """Hand one look to the writer. Never raises, never waits, never keeps a reference to ``frame``. Off, it is
        one attribute read. It times itself: an offer that is slow ``slow_max`` times switches the recorder off."""
        if not self.on:
            return
        t0 = monotonic()
        try:
            n = 0 if frame is None else len(frame)
            rows = []
            if n:
                t = frame.tail(TAIL)
                rows = list(zip(t["ts"].tolist(), t["open"].tolist(), t["high"].tolist(), t["low"].tolist(),
                                t["close"].tolist(), t["volume"].tolist()))
            self.q.put_nowait((str(symbol)[:SYMBOL_MAX], self.clock(), book_now, n, rows, float(ms)))
            self.offered += 1
        except queue.Full:
            self.dropped += 1
            if self.dropped >= self.drop_max:
                self._trip(f"{self.dropped} looks dropped: its writer cannot keep up")
        except Exception:  # noqa: BLE001 - a measurement beside the book: its failure is counted, never raised
            self.errors += 1
            if not self._warned:
                self._warned = True
                log.exception("feed shadow: a look could not be offered (counted; said once)")
        if (monotonic() - t0) * 1000.0 > self.slow_ms:
            self.slow += 1
            if self.slow >= self.slow_max:
                self._trip(f"{self.slow} offers each took over {self.slow_ms:g} ms in the book's thread")

    # ---- the watchdog thread: the switch and the budgets, independent of the writer ----
    def _watch(self) -> None:
        while not self._halt.wait(self.watch_s):
            try:
                if self.on and self.off_file.exists():
                    self._trip("switched off by the OFF file")
                if self.on:
                    size = sum(p.stat().st_size for p in (self.path, Path(str(self.path) + "-wal")) if p.exists())
                    if size > self.max_db_bytes:
                        self._trip(f"its database is {size} bytes, over {self.max_db_bytes}")
                    elif self.path.parent.exists() and self._free(self.path.parent) < self.min_free_bytes:
                        self._trip(f"under {self.min_free_bytes} bytes free on its volume")
            except Exception:  # noqa: BLE001 - a watchdog that cannot look switches the recorder off (fail closed)
                log.exception("feed shadow: the watchdog could not look; recording is switched off")
                self._trip("its watchdog could not look")
            if not self.on:
                self._say()
                return

    def _say(self) -> None:
        """Once: the reason in the log, and the OFF file (with the reason) so a restart does not start it again."""
        if self._said:
            return
        self._said = True
        log.error("feed shadow: OFF (%s). The books are not affected. It stays off while %s exists.", self.why,
                  self.off_file)
        try:
            if not self.off_file.exists():
                self.off_file.parent.mkdir(parents=True, exist_ok=True)
                self.off_file.write_text(f"{_iso(self.clock())} {self.why}\n", encoding="utf-8")
        except Exception:  # noqa: BLE001 - e.g. the volume is full: it is off in this process all the same
            log.exception("feed shadow: the OFF file could not be written (it is off until this process ends)")

    # ---- the writer thread ----
    def start(self) -> "Recorder":
        self._thread.start()
        self._watcher.start()
        return self

    def stop(self, timeout: float = 10.0) -> None:
        """Flush and end the writer (tests, and a clean shutdown). The book never calls this."""
        self._halt.set()
        try:
            self.q.put(_STOP, timeout=timeout)
        except queue.Full:
            pass
        self._thread.join(timeout)

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path, timeout=5.0)
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript(SCHEMA)
        old = (self.clock().astimezone(ET).date() - timedelta(days=KEEP_DAYS)).isoformat()
        c.execute("DELETE FROM looks WHERE session < ?", (old,))
        c.execute("DELETE FROM bars WHERE session < ?", (old,))
        c.execute("DELETE FROM health WHERE at < ?", (old,))
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('version', ?)", (VERSION,))
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('started_at', ?)", (_iso(self.clock()),))
        c.execute("DELETE FROM meta WHERE key IN ('off_at', 'off_why')")       # a fresh start: it is on again
        c.commit()
        return c

    def _closing(self, c: sqlite3.Connection) -> None:
        """The writer's last act once the recorder is off: say so in its own database, if it still can."""
        try:
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('off_at', ?)", (_iso(self.clock()),))
            c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES ('off_why', ?)", (str(self.why)[:300],))
            c.execute("INSERT OR REPLACE INTO health(at, offered, written, dropped, errors, fails, waiting) "
                      "VALUES (?,?,?,?,?,?,?)", (_iso(self.clock()), self.offered, self.written, self.dropped,
                                                 self.errors, self.fails, self.q.qsize()))
            c.commit()
            c.close()
        except Exception:  # noqa: BLE001 - it is off either way; the log already says why
            log.exception("feed shadow: its last note could not be written")

    def _run(self) -> None:
        try:
            c = self._open()
        except Exception:  # noqa: BLE001 - no database, no recording; the book is not told and is not affected
            self._trip("its database could not be opened")
            log.exception("feed shadow: its database could not be opened; recording is off")
            return
        last_look: dict[str, str] = {}                       # symbol -> the previous look's time (this process)
        cache: dict[str, dict[str, tuple]] = {}              # symbol -> {candle ts: (values, closed)} of recent looks
        beat = monotonic()
        while True:
            if not self.on:                                  # switched off (the OFF file, a trip): stop writing
                self._closing(c)
                return
            batch, stop = [], False
            try:
                batch.append(self.q.get(timeout=FLUSH_S))
                while len(batch) < 500:
                    batch.append(self.q.get_nowait())
            except queue.Empty:
                pass
            if any(x is _STOP for x in batch):
                batch, stop = [x for x in batch if x is not _STOP], True
            try:
                for item in batch:
                    self._write(c, item, last_look, cache)
                if monotonic() - beat >= 60 or stop:
                    beat = monotonic()
                    c.execute("INSERT OR REPLACE INTO health(at, offered, written, dropped, errors, fails, waiting) "
                              "VALUES (?,?,?,?,?,?,?)", (_iso(self.clock()), self.offered, self.written, self.dropped,
                                                         self.errors, self.fails, self.q.qsize()))
                c.commit()
                self.fails = 0
            except Exception:  # noqa: BLE001 - a failed write is counted; after MAX_FAILS the recorder is off
                self.fails += 1
                log.exception("feed shadow: write failed (%d in a row)", self.fails)
                try:
                    c.rollback()
                except sqlite3.Error:
                    pass
                if self.fails >= MAX_FAILS:
                    self._trip(f"{self.fails} failed writes in a row")
                    log.error("feed shadow: switched off after %d failed writes in a row", self.fails)
                    return
            if stop:
                c.close()
                return

    def _write(self, c: sqlite3.Connection, item: tuple, last_look: dict, cache: dict) -> None:
        symbol, at, book_now, n, rows, ms = item
        at_ts = pd.Timestamp(at)
        at_s, session = _iso(at_ts), at_ts.tz_convert(ET).date().isoformat()
        prev = last_look.get(symbol)
        good = []
        for ts, o, h, lo, cl, v in rows:                     # external data: a row that is not a candle is left out
            try:
                t = pd.Timestamp(ts)
                vals = tuple(float(x) for x in (o, h, lo, cl, v))
            except (TypeError, ValueError):
                continue
            if t.tzinfo is None or pd.isna(t) or not all(math.isfinite(x) for x in vals):
                continue
            good.append((t, vals))
        c.execute("INSERT INTO looks(symbol, session, at, book_now, n_bars, last_ts, ms) VALUES (?,?,?,?,?,?,?)",
                  (symbol, session, at_s, _iso(book_now) if book_now is not None else None, int(n),
                   _iso(good[-1][0]) if good else None, ms))
        mem = cache.setdefault(symbol, {})
        for t, vals in good:
            closed = at_ts >= t + BAR
            key = _iso(t)
            if mem.get(key) in ((vals, closed), (vals, True)):
                continue                                     # nothing new about this candle since the last look
            c.execute(UPSERT, {"symbol": symbol, "ts": key, "session": session, "at": at_s,
                               "closed_at": at_s if closed else None, "closed_prev": prev if closed else None,
                               "o": vals[0], "h": vals[1], "l": vals[2], "c": vals[3], "v": vals[4]})
            mem[key] = (vals, closed or mem.get(key, (None, False))[1])
        for key in sorted(mem)[:-2 * TAIL]:                   # bounded: only the recent candles of a symbol are remembered
            del mem[key]
        if len(cache) > 2000:                                # bounded across symbols (a day has far fewer)
            cache.clear()
        if len(last_look) > 5000:                            # bounded too (review 2026-11-27): the next look of each
            last_look.clear()                                # symbol is then a first look again (censored, not wrong)
        last_look[symbol] = at_s
        self.written += 1


def start(path: Path = DB) -> Recorder | None:
    """The recorder when ``SGT_FEED_SHADOW=1``, else None. Never raises: a recorder that cannot start is no recorder."""
    try:
        if os.environ.get(ENV) != "1":
            return None
        if (Path(path).parent / OFF_NAME).exists():          # switched off by hand or by itself: stays off
            log.warning("feed shadow: not started, %s exists", Path(path).parent / OFF_NAME)
            return None
        return Recorder(path).start()
    except Exception:  # noqa: BLE001 - see the module doc: the listener must start whatever happens here
        log.exception("feed shadow: not started")
        return None


def tap(rec: Recorder | None, bars: Callable) -> Callable:
    """``bars`` with every frame it returns also offered to ``rec``. With no recorder the reader itself is returned.
    The frame is returned unchanged and an error of the recorder never reaches the caller."""
    if rec is None:
        return bars

    def tapped(symbol, now):
        t0 = monotonic()
        f = bars(symbol, now)
        try:
            rec.offer(symbol, now, f, (monotonic() - t0) * 1000.0)
        except Exception:  # noqa: BLE001 - offer never raises; this guard is for whatever replaces it
            pass
        return f
    return tapped


# ---------------------------------------------------------------- the nightly comparison (pure; no I/O) ----
CLASSES = ("no_trade_on_tape", "tape_only", "delivered", "never_held", "live_only")
LAG_EDGES = (5, 15, 30, 60)      # seconds after the minute ended: upper bound of when the book first held the candle


def session_minutes(session: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(first minute, the close) of the regular session, from the exchange calendar (an early close ends early)."""
    from sgt.intraday.calendar import default_calendar

    b = default_calendar().bounds(date.fromisoformat(session))
    return pd.Timestamp(b.rth_open), pd.Timestamp(b.rth_close)


def classify(looks: list[pd.Timestamp], live: dict[pd.Timestamp, dict], free: dict[pd.Timestamp, tuple],
             tape: dict[pd.Timestamp, tuple], first: pd.Timestamp, close: pd.Timestamp) -> list[dict]:
    """One row per WATCHED minute of one symbol. A minute is watched when the book looked at the symbol before the
    minute ended and again 60 s or more after it ended: only then could it have held the candle and did it have the
    time to be given it. ``live``: candle start -> the recorder's row (``closed_at``, ``closed_prev_look``, values).
    ``free`` / ``tape``: candle start -> (o, h, l, c, v) of the vendor's FINISHED record, free feed / full tape.

    The class says what a missing live candle was:
      no_trade_on_tape  no candle anywhere: nothing traded, as far as both finished records show
      tape_only         the tape traded; the free feed's exchange did not: no candle was possible on this feed
      delivered         the free feed's record has it and the book held it closed (``lag_hi``: seconds after the
                        minute ended by which it was held; ``lag_lo``: it was NOT yet held this long after, when a
                        look in between proves that; None when no look fell in between)
      never_held        the free feed's record has it and the book never held it: missing data
      live_only         the book held a closed candle the free feed's finished record does not have
    Extra flags, kept apart because they question the records themselves and are not assumed away:
      free_not_on_tape  the free feed's record has a candle for a minute the full-tape record has none for
      outside_tape      the free feed's high or low lies outside the full tape's range for that minute
      changed           the candle the book held closed differs in PRICE (open, high, low, close) from the free
                        feed's finished candle; ``volume_changed`` is kept apart (late prints add volume routinely)
    """
    out, looks = [], sorted(looks)
    if not looks:
        return out
    m = max(first, looks[0].floor("min"))
    while m + BAR <= close:
        end = m + BAR
        if looks[0] > end or looks[-1] < end + BAR:
            m += BAR
            continue
        row, got = {"ts": m}, live.get(m)
        held = got is not None and got.get("closed_at") is not None
        in_free, in_tape = m in free, m in tape
        if held and in_free:
            row["cls"] = "delivered"
        elif held:
            row["cls"] = "live_only"
        elif in_free:
            row["cls"] = "never_held"
        elif in_tape:
            row["cls"] = "tape_only"
        else:
            row["cls"] = "no_trade_on_tape"
        if held:
            row["lag_hi"] = (pd.Timestamp(got["closed_at"]) - end).total_seconds()
            prev = got.get("closed_prev_look")
            prev_s = (pd.Timestamp(prev) - end).total_seconds() if prev else None
            row["lag_lo"] = prev_s if prev_s is not None and prev_s >= 0 else None
            row["censored"] = prev is None           # the symbol's first look in this process: the delay is unknown
            if in_free:                              # prices and volume apart (review 2026-11-27): a live candle's
                # volume is routinely below the finished candle's (late prints), which is not a price disagreement
                row["changed"] = any(abs(a - b) > 1e-9 for a, b in zip(got["values"][:4], free[m][:4]))
                row["volume_changed"] = abs(got["values"][4] - free[m][4]) > 1e-9
        if in_free and not in_tape:
            row["free_not_on_tape"] = True
        if in_free and in_tape and (free[m][1] > tape[m][1] + 1e-9 or free[m][2] < tape[m][2] - 1e-9):
            row["outside_tape"] = True
        out.append(row)
        m += BAR
    return out


def stop_seen(entry_ts: pd.Timestamp, stop: float, side: str, live: dict[pd.Timestamp, dict],
              tape: dict[pd.Timestamp, tuple], until: pd.Timestamp) -> dict:
    """When the full tape's candles first show a trade through ``stop`` after ``entry_ts`` (minute resolution), and
    when the book first held a closed live candle showing one. ``lag_s``: from the END of the tape's minute to the
    look that held the live candle; None when the live feed never showed it before ``until``."""
    hit = (lambda v: v[2] <= stop) if side == "long" else (lambda v: v[1] >= stop)
    on_tape = next((m for m in sorted(tape) if entry_ts <= m < until and hit(tape[m])), None)
    seen = None
    for m in sorted(live):
        got = live[m]
        if entry_ts <= m < until and got.get("closed_at") is not None and hit(got["values"]):
            seen = (m, pd.Timestamp(got["closed_at"]))
            break
    out = {"tape_minute": on_tape.isoformat() if on_tape is not None else None,
           "live_minute": seen[0].isoformat() if seen else None, "live_seen_at": seen[1].isoformat() if seen else None,
           "lag_s": None}
    if on_tape is not None and seen is not None:
        out["lag_s"] = (seen[1] - (on_tape + BAR)).total_seconds()
    return out


def summarize(rows: list[dict]) -> dict:
    """Counts over the watched minutes of every symbol of a session (``classify`` rows, each with ``symbol``)."""
    n = {k: sum(r["cls"] == k for r in rows) for k in CLASSES}
    held = [r for r in rows if r["cls"] == "delivered" and not r.get("censored")]
    lags = sorted(r["lag_hi"] for r in held)
    q = lambda p: (lags[min(len(lags) - 1, int(p * len(lags)))] if lags else None)   # noqa: E731
    no_live = n["no_trade_on_tape"] + n["tape_only"] + n["never_held"]
    traded = n["tape_only"] + n["delivered"] + n["never_held"]      # minutes with a candle on either finished record
    return {
        "watched_minutes": len(rows), **n,
        "no_live_candle": no_live,
        # of the minutes the book had no candle for: what each one was (the three add up to no_live_candle)
        "no_live_candle_was": {"no_trade_on_tape": n["no_trade_on_tape"], "tape_only": n["tape_only"],
                               "never_held": n["never_held"]},
        "tape_traded_minutes": traded,
        "free_feed_share_of_tape_minutes": round((n["delivered"] + n["never_held"]) / traded, 4) if traded else None,
        "delivered_timed": len(held),
        "lag_hi_s": {"median": q(0.5), "p95": q(0.95), "max": lags[-1] if lags else None},
        "held_within_s": {str(e): sum(x <= e for x in lags) for e in LAG_EDGES},
        "proved_later_than_s": {str(e): sum(1 for r in held if r.get("lag_lo") is not None and r["lag_lo"] >= e)
                                for e in LAG_EDGES},
        "changed": sum(bool(r.get("changed")) for r in rows),
        "volume_changed": sum(bool(r.get("volume_changed")) for r in rows),
        "free_not_on_tape": sum(bool(r.get("free_not_on_tape")) for r in rows),
        "outside_tape": sum(bool(r.get("outside_tape")) for r in rows),
    }
