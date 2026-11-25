"""Ops state for SMS control (2026-11-02): approvals, inbound texts, flags, audit, approved book.

One SQLite file on the runner volume (``reports/ops/ops.db``). Every state transition is a single
transaction with a status precondition (``UPDATE ... WHERE status = ?``), so the per-minute poller
and the scheduled jobs can run concurrently without double-approving or double-executing.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path("reports/ops/ops.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, strategy TEXT NOT NULL,
  kind TEXT NOT NULL, period TEXT NOT NULL, summary TEXT NOT NULL, payload TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL, status TEXT NOT NULL,
  decided_at TEXT, decided_sid TEXT, result TEXT);
CREATE INDEX IF NOT EXISTS ix_approvals_status ON approvals(status, strategy);
CREATE TABLE IF NOT EXISTS inbound(sid TEXT PRIMARY KEY, sent_at TEXT, body TEXT, handled_at TEXT, outcome TEXT);
CREATE TABLE IF NOT EXISTS flags(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, event TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, book TEXT NOT NULL, ref INTEGER, symbol TEXT,
  state_from TEXT, state_to TEXT, reason TEXT, facts TEXT, config TEXT);
CREATE INDEX IF NOT EXISTS ix_decisions_ref ON decisions(book, ref);
CREATE TABLE IF NOT EXISTS alert_events(
  channel TEXT NOT NULL, message_id INTEGER NOT NULL, symbol TEXT NOT NULL, alert_type TEXT NOT NULL,
  posted_at TEXT NOT NULL, received_at TEXT NOT NULL, via TEXT, recovered_at TEXT, status TEXT NOT NULL, reason TEXT,
  fade_id INTEGER, PRIMARY KEY(channel, message_id, symbol, alert_type));
CREATE INDEX IF NOT EXISTS ix_alert_events_posted ON alert_events(posted_at);
CREATE TABLE IF NOT EXISTS alert_gaps(
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, channel TEXT NOT NULL, from_id INTEGER, to_id INTEGER,
  n INTEGER, reason TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS book_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT NOT NULL, symbol TEXT NOT NULL, target REAL NOT NULL,
  fill_session TEXT NOT NULL, source TEXT NOT NULL, approval_id INTEGER, status TEXT NOT NULL,
  fill_price REAL, created_at TEXT NOT NULL, label TEXT);
CREATE TABLE IF NOT EXISTS book_nav(
  strategy TEXT NOT NULL, session TEXT NOT NULL, nav REAL NOT NULL, ret REAL NOT NULL, holdings TEXT NOT NULL,
  PRIMARY KEY(strategy, session));
CREATE TABLE IF NOT EXISTS book_legs(
  strategy TEXT NOT NULL, session TEXT NOT NULL, symbol TEXT NOT NULL, weight REAL NOT NULL, usd REAL NOT NULL,
  close REAL NOT NULL, PRIMARY KEY(strategy, session, symbol));
CREATE TABLE IF NOT EXISTS fade_trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, session TEXT NOT NULL, alert_type TEXT NOT NULL,
  posted_at TEXT NOT NULL, channel TEXT NOT NULL, message_id INTEGER NOT NULL, status TEXT NOT NULL,
  reason TEXT, weight REAL, est_entry REAL, est_entry_at TEXT, est_last REAL, est_exit REAL, est_exit_at TEXT,
  entry_ts TEXT, entry REAL, cover REAL, cost_rt REAL, gross REAL, net REAL, settled_at TEXT,
  edited INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fade_event ON fade_trades(symbol, session) WHERE status != 'filtered';
CREATE TABLE IF NOT EXISTS scan_trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, session TEXT NOT NULL, bar_ts TEXT NOT NULL,
  detected_at TEXT NOT NULL, signal_px REAL NOT NULL, status TEXT NOT NULL, reason TEXT, weight REAL NOT NULL,
  est_entry REAL, est_entry_at TEXT, est_last REAL, est_exit REAL, est_exit_at TEXT,
  entry_ts TEXT, entry REAL, cover REAL, cost_rt REAL, gross REAL, net REAL, settled_at TEXT, created_at TEXT NOT NULL,
  UNIQUE(symbol, session));
CREATE INDEX IF NOT EXISTS ix_scan_status ON scan_trades(status, session);
CREATE TABLE IF NOT EXISTS trade_notes(key TEXT PRIMARY KEY, text TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trade_notes_history(
  id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS trade_ai(
  key TEXT PRIMARY KEY, model TEXT, at TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
  body TEXT);
CREATE TABLE IF NOT EXISTS trade_insights(
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_at TEXT NOT NULL, model TEXT NOT NULL, scope TEXT NOT NULL,
  body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_fade_status ON fade_trades(status, session);
CREATE INDEX IF NOT EXISTS ix_fade_message ON fade_trades(channel, message_id);
CREATE TABLE IF NOT EXISTS symbol_beta(
  symbol TEXT NOT NULL, session TEXT NOT NULL, beta REAL, n_obs INTEGER, computed_at TEXT NOT NULL,
  PRIMARY KEY(symbol, session));
CREATE TABLE IF NOT EXISTS long_trades(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fade_id INTEGER NOT NULL UNIQUE, symbol TEXT NOT NULL, session TEXT NOT NULL,
  kind TEXT, status TEXT NOT NULL, trigger REAL, stop REAL, target REAL, weight REAL, entry_ts TEXT, est_entry REAL,
  est_last REAL, est_exit REAL, est_exit_at TEXT, reason TEXT, net REAL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS ix_long_status ON long_trades(status, session);
CREATE TABLE IF NOT EXISTS long_armed(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fade_id INTEGER NOT NULL, symbol TEXT NOT NULL, session TEXT NOT NULL,
  ready TEXT NOT NULL, kinds TEXT, ref REAL, trigger REAL, stop REAL, texted_at TEXT, note TEXT, px REAL, px_at TEXT,
  px_note TEXT, created_at TEXT NOT NULL, UNIQUE(fade_id, ready));
CREATE TABLE IF NOT EXISTS long_shadow(
  id INTEGER PRIMARY KEY AUTOINCREMENT, fade_id INTEGER NOT NULL, symbol TEXT NOT NULL, session TEXT NOT NULL,
  kind TEXT NOT NULL, side TEXT, ready TEXT, trigger REAL, stop REAL, entry_ts TEXT, entry REAL, exit_ts TEXT,
  exit REAL, reason TEXT, gross REAL, net REAL, cost_rt REAL, created_at TEXT NOT NULL, UNIQUE(fade_id, kind));
"""
# Additive column migrations for tables that already exist on the runner's volume (CREATE IF NOT EXISTS
# never adds columns). Only ADD COLUMN with NULL defaults: old rows stay valid, old code ignores them.
COLUMNS = {
    "fade_trades": {                         # 2026-11-04 plan W1: borrow reality at alert time
        "ib_avail": "INTEGER", "ib_fee": "REAL", "ib_snap": "TEXT", "alp_etb": "INTEGER", "alp_shortable": "INTEGER",
        "rule_entry": "REAL", "rule_net": "REAL",   # 2026-11-04 plan W5: official result at the tested timing
        "news_own": "INTEGER", "news_earn": "INTEGER", "news_head": "TEXT",   # prereg_fade_news_2026-11-05
        "conf": "INTEGER",                   # confidence shown in the entry text (0-100)
        "filing_dilution": "INTEGER", "filing_form": "TEXT", "filing_8k": "INTEGER",  # EDGAR at alert, 2026-11-05
        "st_n60": "INTEGER", "st_n15": "INTEGER", "st_span": "REAL", "st_bull": "INTEGER",  # StockTwits at alert,
        "st_bear": "INTEGER", "st_watch": "INTEGER",                                         # 2026-11-06 (forward)
        "x_n60": "INTEGER", "x_n15": "INTEGER", "x_authors": "INTEGER", "x_eng": "INTEGER",  # X at alert, 2026-11-08
        "x_cat24": "INTEGER", "x_cat_posts": "TEXT",        # X catalyst check at entry, 2026-11-09 (prereg_fade_x A2)
        "rd_rank": "INTEGER", "rd_mentions": "INTEGER", "rd_upvotes": "INTEGER",              # Reddit (ApeWisdom)
        "rd_mentions_prev": "INTEGER",                                                         # at alert, 2026-11-08
        "beta": "REAL",                      # amendment F7 (2026-11-10): beta at entry; >= 3 is not shorted
        # 2026-11-18 (sgt.fast.scenario): where in its move the stock was at the alert, from official bars, nightly
        "scen_run": "REAL", "scen_off": "REAL", "scen_band": "TEXT", "scen_where": "TEXT", "scen_note": "TEXT",
        # owner 2026-11-18 (option A): what an alert the book did NOT take would have made (sgt.fast.would_have)
        "wh_entry": "REAL", "wh_net": "REAL", "wh_note": "TEXT",
        # owner 2026-11-19 ("connect to reddit and news articles and sources"): the free sources read for EVERY alert at
        # alert time (sgt.fast.alert_context), not only for a short that is entered. news_kind: sgt.fast.news.kind_of
        # The news read lands in its OWN columns: news_own IS NULL is how the short book's news gate (fade F6) knows it
        # has not judged an alert yet, so this read must never fill news_own / news_earn.
        "news_kind": "TEXT", "ctx_news_own": "INTEGER", "ctx_news_earn": "INTEGER", "ctx_head": "TEXT",
        "ctx_at": "TEXT", "ctx_note": "TEXT",
        # audit 2026-11-21 (sgt.fast.exec_check): the same short from the first official minute after the bot's own
        # decision (the official entry assumes alert + 10 s; the alert often reaches us later). A measurement.
        "dec_entry": "REAL", "dec_net": "REAL", "dec_note": "TEXT",
        # 2026-11-22 (sgt.fast.alert_recovery): how the alert reached the book ("push", "poll", or "replay" = posted
        # while the listener was down and found by the catch-up) and, for a replay, when it was recovered. The entry
        # of a recovered alert never uses a print older than recovered_at.
        "via": "TEXT", "recovered_at": "TEXT",
        # 2026-11-24 (GLXG: the live entry came 2 min 32 s after the tested bar and nothing on record said why): the
        # time of the print the live entry price came from, and why the entry waited the first time it had no price.
        # A record only: what the live code does at entry is unchanged. Whether a live entry that comes after the
        # registered entry bar is eligible is NOT settled by this (see the note in ``fade._enter``).
        "est_print_at": "TEXT", "entry_wait": "TEXT",
    },
    "scan_trades": {"beta": "REAL"},         # owner 2026-11-10 ground rule: beta >= 3 first (sgt.fast.beta)
    "long_trades": {                         # 2026-11-11: official result = the live order replayed on SIP bars
        "entry": "REAL", "fill_ts": "TEXT", "exit": "REAL", "exit_ts": "TEXT", "exit_reason": "TEXT", "gross": "REAL",
        "cost_rt": "REAL", "settled_at": "TEXT",
        "rules": "TEXT",                     # 2026-11-12: the long-book rule version a trade opened under (None = before A2)
        # 2026-11-18 (sgt.fast.text_px): the official price when the fill text went out, and buying there
        "text_px": "REAL", "text_px_at": "TEXT", "text_how": "TEXT", "text_gross": "REAL",
        # 2026-11-18 (sgt.fast.trend_matrix): where the trader's rule would have left this long (MACD going red)
        "tr_exit_px": "REAL", "tr_exit_at": "TEXT", "tr_why": "TEXT",
        # audit 2026-11-21 (sgt.fast.exec_check): the low of the official minute of a stop exit (the official result
        # books the stop price; a real stop order in a falling thin stock fills between the two). A measurement.
        "stop_low": "REAL"},
    # 2026-11-11 (owner: notes feed Luna): the note version a review saw, and re-reviews spent on new notes
    "trade_ai": {"note_at": "TEXT", "note_reviews": "INTEGER"},
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    """UTC ISO-8601: every stored timestamp uses this so SQL string comparisons order correctly."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime")
    return ts.astimezone(timezone.utc).isoformat()


SLOW_S = 0.25                       # a wait for the write lock, or a hold of it, this long is written down
WAITS_NAME = "db_waits.jsonl"       # next to the database
READ_CLOSE = "close of a read"      # ``what`` of a line that timed the close of a read block, not a write block
WAITS_MAX_BYTES, WAITS_MAX_LINES = 2_000_000, 20_000
DECIDE_TIMEOUT_S = 0.5              # the longest the decision record may hold a trading loop (see OpsStore.decide)
SPOOL_NAME = "decisions.spool.jsonl"   # next to the database: lines that could not be written, waiting for a replay
SPOOL_MAX_BYTES, SPOOL_MAX_LINES = 5_000_000, 20_000


class OpsStore:
    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.decide_lost = 0                # decision lines this process could neither write nor spool
        c = sqlite3.connect(path, timeout=30)
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)             # executescript manages its own COMMIT
            for table, cols in COLUMNS.items():
                have = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - fixed names
                for name, typ in cols.items():
                    if name not in have:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")  # noqa: S608 - fixed names
            c.commit()
        finally:
            c.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction. It takes the database's single write lock at once (BEGIN IMMEDIATE) and keeps it
        for the whole block, so everything inside must be quick: no network, no file reads. A block that only READS
        belongs in ``read()``, which takes no write lock. Measured 2026-11-22 (docs/findings/
        ops_risks_2026-11-22.md): a held lock delays every other writer by as long as it is held, the stop check of
        an open trade included. A wait for the lock, or a hold of it, of SLOW_S or more is written down with who."""
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        t0, held = time.monotonic(), None
        try:
            c.execute("BEGIN IMMEDIATE")
            held = time.monotonic()
            yield c
            c.execute("COMMIT")
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()
            end = time.monotonic()
            wait, hold = (held or end) - t0, (end - held) if held is not None else 0.0
            if wait >= SLOW_S or hold >= SLOW_S:
                self._note_slow(wait, hold, held is None, sys._getframe(2))

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """A consistent READ that takes no write lock. The database runs in WAL mode, where a reader and the writer do
        not block each other: a page or a report built inside this block cannot delay a trading write, however long
        it takes. Writes are refused (query_only). Use it for every block that only selects.

        Its CLOSE is timed (2026-11-24). Every block here opens a connection of its own, and SQLite moves the pending
        writes into the database file (a checkpoint, with its disk syncs) when the LAST open connection closes: a
        read block too (reproduced). That work was in no record: the 1.3 s wait of 11-24 08:00:22 ET has no holder
        in the file. A close of SLOW_S or more is now written down like a slow hold, marked ``what``."""
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA query_only=ON")
            c.execute("BEGIN")                      # deferred: one snapshot from the first select on
            yield c
        finally:
            if c.in_transaction:
                c.execute("ROLLBACK")
            t0 = time.monotonic()
            c.close()
            took = time.monotonic() - t0
            if took >= SLOW_S:
                self._note_slow(0.0, took, False, sys._getframe(2), what=READ_CLOSE)

    def _note_slow(self, wait: float, hold: float, failed: bool, frame, what: str | None = None) -> None:
        """One line per slow wait or hold, next to the database (bounded; never raises: it runs on trading paths)."""
        try:
            via = []                            # a store helper (set_flag, audit, ...) is not the answer to "who":
            while frame.f_back is not None and Path(frame.f_code.co_filename).name == "store.py" and len(via) < 4:
                via.append(frame.f_code.co_name)    # ... name the code that called it
                frame = frame.f_back
            who = f"{Path(frame.f_code.co_filename).name}:{frame.f_code.co_name}:{frame.f_lineno}"
            who += f" via {'/'.join(reversed(via))}" if via else ""
            p = Path(self.path).with_name(WAITS_NAME)
            if p.exists() and p.stat().st_size > WAITS_MAX_BYTES:
                return
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"at": utcnow().isoformat(), "wait": round(wait, 3), "hold": round(hold, 3),
                                    "who": who, "failed": failed, **({"what": what} if what else {})}) + "\n")
            logging.getLogger(__name__).warning("ops database: waited %.2f s, held %.2f s (%s)", wait, hold, who)
        except Exception:  # noqa: BLE001 - a measurement must never break the write it measures
            logging.getLogger(__name__).exception("ops database: slow wait not recorded")

    def slow(self, day: str) -> list[dict]:
        """The slow waits and holds written down on one UTC day (ISO date); [] when there were none."""
        p = Path(self.path).with_name(WAITS_NAME)
        out = []
        try:
            for raw in (p.read_text(encoding="utf-8").splitlines() if p.exists() else [])[-WAITS_MAX_LINES:]:
                try:
                    r = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(r, dict) and str(r.get("at", "")).startswith(day):
                    out.append(r)
        except OSError:
            logging.getLogger(__name__).exception("ops database: the record of slow waits cannot be read")
        return out

    def audit(self, event: str, **detail: Any) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO audit(at, event, detail) VALUES (?,?,?)",
                      (utcnow().isoformat(), event, json.dumps(detail, default=str)[:4000]))

    def decide(self, book: str, ref: int | None, symbol: str | None, state_from: str | None, state_to: str | None,
               reason: str | None = None, config: str | None = None, **facts: Any) -> None:
        """One line of the decision record (audit 2026-11-22): what a book decided about one alert or trade, WHEN it
        decided it, with the facts it had then. Append-only, written at the moment of the decision, so a later review
        reads what was known instead of reconstructing it from the outcome.

        Failure behaviour (audit 2026-11-22, phase 3b). It never raises and never holds a trading loop: the write
        uses its own connection with a DECIDE_TIMEOUT_S wait, where every other write waits 30 s. A line that cannot
        be written (database busy, table missing, disk error) goes to the spool file next to the database with its
        original time and is put back by ``decide_replay`` (the nightly health check runs it). Only when the spool
        cannot be written either is the line lost: logged as an error and counted in ``decide_lost``. Nothing here
        is silent: ``decision_health`` compares the record with the books' own rows every night."""
        try:
            line = [utcnow().isoformat(), book, ref, symbol, state_from, state_to,
                    None if reason is None else str(reason)[:300],
                    json.dumps({k: v for k, v in facts.items() if v is not None}, default=str)[:2000], config]
        except Exception:  # noqa: BLE001 - a fact that cannot be written down must not cost the trade
            logging.getLogger(__name__).exception("decision record: line not built for %s %s", book, ref)
            self.decide_lost += 1
            return
        try:
            self._decide_write([line], DECIDE_TIMEOUT_S)
        except Exception as e:  # noqa: BLE001 - observability only: never raises into a trading path
            self._decide_spool(line, e)

    def _decide_write(self, lines: list[list], timeout: float) -> int:
        """Insert decision lines in one transaction on a connection of their own; lines already there are skipped."""
        c = sqlite3.connect(self.path, timeout=timeout, isolation_level=None)
        try:
            c.execute("BEGIN IMMEDIATE")
            n = 0
            for ln in lines:
                if c.execute("SELECT 1 FROM decisions WHERE at=? AND book=? AND ref IS ? AND state_to IS ? LIMIT 1",
                             (ln[0], ln[1], ln[2], ln[5])).fetchone() is None:
                    c.execute("INSERT INTO decisions(at, book, ref, symbol, state_from, state_to, reason, facts, "
                              "config) VALUES (?,?,?,?,?,?,?,?,?)", ln)
                    n += 1
            c.execute("COMMIT")
            return n
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    @property
    def spool_path(self) -> Path:
        return Path(self.path).with_name(SPOOL_NAME)

    def _decide_spool(self, line: list, err: Exception) -> None:
        log = logging.getLogger(__name__)
        try:
            p = self.spool_path
            if p.exists() and p.stat().st_size > SPOOL_MAX_BYTES:
                raise OSError("decision spool is full")
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"line": line, "error": repr(err)[:200]}) + "\n")
            log.warning("decision record: line for %s %s kept in the spool (%r)", line[1], line[2], err)
        except Exception:  # noqa: BLE001 - last resort: say so loudly, count it, never raise
            self.decide_lost += 1
            log.exception("DECISION RECORD LINE LOST for %s %s (database: %r)", line[1], line[2], err)

    def spooled(self) -> int:
        """Decision lines waiting in the spool (0 = none; -1 = the spool cannot be read)."""
        try:
            return sum(sum(1 for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip())
                       for p in (self.spool_path, self.spool_path.with_suffix(".replaying")) if p.exists())
        except OSError:
            return -1

    def decide_replay(self) -> int:
        """Put spooled lines back into the record (recovery; run by the nightly health check, safe to run by hand).
        The spool is renamed first, so lines written meanwhile go to a fresh file; a line already in the table is
        skipped, so a replay that died after its commit cannot double a line. On any failure the renamed file stays
        and the next run retries it. Returns the number of lines inserted."""
        work = self.spool_path.with_suffix(".replaying")
        try:
            if self.spool_path.exists():
                if work.exists():                       # an earlier replay did not finish: take both, oldest first
                    with work.open("a", encoding="utf-8") as f:
                        f.write(self.spool_path.read_text(encoding="utf-8"))
                    self.spool_path.unlink()
                else:
                    self.spool_path.rename(work)
            if not work.exists():
                return 0
            lines = []
            for raw in work.read_text(encoding="utf-8").splitlines()[:SPOOL_MAX_LINES]:
                try:
                    ln = json.loads(raw).get("line")
                except (ValueError, AttributeError):
                    continue                                # a torn last line of a crash: nothing to recover from it
                if isinstance(ln, list) and len(ln) == 9:
                    lines.append(ln)
            c = sqlite3.connect(self.path, timeout=30)      # the table may be the thing that was missing
            try:
                c.executescript(SCHEMA)                     # every statement is IF NOT EXISTS, as at start-up
            finally:
                c.close()
            n = self._decide_write(lines, 30)
            work.unlink()
            return n
        except Exception:  # noqa: BLE001 - recovery must not break the job that calls it; the file stays for a retry
            logging.getLogger(__name__).exception("decision record: replay of the spool failed (it stays for a retry)")
            return 0

    def decisions(self, book: str, ref: int) -> list[dict]:
        """The decision record of one alert or trade, oldest first."""
        with self.tx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM decisions WHERE book=? AND ref=? ORDER BY id", (book, ref))]

    def flag(self, key: str, default: str | None = None) -> str | None:
        """One flag's value. A READ: it takes no write lock (2026-11-23). Every switch the books consult (paused, the
        off flags, arming) goes through here, on the stop-check path too, and used to queue behind any writer."""
        with self.read() as c:
            r = c.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_flag(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO flags(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
                      "SET value=excluded.value, updated_at=excluded.updated_at", (key, value, utcnow().isoformat()))

    def take(self, key: str, amount: int, cap: int) -> bool:
        """Atomically add ``amount`` to the integer counter ``key`` if the result stays <= ``cap``; True when taken.
        One transaction (BEGIN IMMEDIATE), so concurrent processes cannot both pass a cap check (adversarial F-5/F-6)."""
        with self.tx() as c:
            r = c.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
            cur = int(r["value"]) if r and str(r["value"]).lstrip("-").isdigit() else 0
            if cur + amount > cap:
                return False
            c.execute("INSERT INTO flags(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
                      "SET value=excluded.value, updated_at=excluded.updated_at", (key, str(cur + amount),
                                                                                   utcnow().isoformat()))
        return True

    def add(self, key: str, amount: int) -> int:
        """Atomically add to an integer counter (no cap); returns the new value."""
        with self.tx() as c:
            r = c.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
            new = (int(r["value"]) if r and str(r["value"]).lstrip("-").isdigit() else 0) + amount
            c.execute("INSERT INTO flags(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
                      "SET value=excluded.value, updated_at=excluded.updated_at", (key, str(new), utcnow().isoformat()))
        return new

    def paused(self) -> bool:
        return self.flag("paused", "0") == "1"

    def count_events(self, event: str, since: datetime) -> int:
        with self.tx() as c:
            return int(c.execute("SELECT COUNT(*) FROM audit WHERE event=? AND at>=?",
                                 (event, iso(since))).fetchone()[0])
