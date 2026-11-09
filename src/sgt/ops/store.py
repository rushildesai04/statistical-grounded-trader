"""Ops state for SMS control (2026-11-02): approvals, inbound texts, flags, audit, approved book.

One SQLite file on the runner volume (``reports/ops/ops.db``). Every state transition is a single
transaction with a status precondition (``UPDATE ... WHERE status = ?``), so the per-minute poller
and the scheduled jobs can run concurrently without double-approving or double-executing.
"""
from __future__ import annotations

import json
import sqlite3
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
CREATE TABLE IF NOT EXISTS book_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT, strategy TEXT NOT NULL, symbol TEXT NOT NULL, target REAL NOT NULL,
  fill_session TEXT NOT NULL, source TEXT NOT NULL, approval_id INTEGER, status TEXT NOT NULL,
  fill_price REAL, created_at TEXT NOT NULL, label TEXT);
CREATE TABLE IF NOT EXISTS book_nav(
  strategy TEXT NOT NULL, session TEXT NOT NULL, nav REAL NOT NULL, ret REAL NOT NULL, holdings TEXT NOT NULL,
  PRIMARY KEY(strategy, session));
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
        "rd_rank": "INTEGER", "rd_mentions": "INTEGER", "rd_upvotes": "INTEGER",              # Reddit (ApeWisdom)
        "rd_mentions_prev": "INTEGER",                                                         # at alert, 2026-11-08
    },
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(ts: datetime) -> str:
    """UTC ISO-8601: every stored timestamp uses this so SQL string comparisons order correctly."""
    if ts.tzinfo is None:
        raise ValueError("naive datetime")
    return ts.astimezone(timezone.utc).isoformat()


class OpsStore:
    def __init__(self, path: Path = DB_PATH) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
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
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.execute("COMMIT")
        except BaseException:
            if c.in_transaction:
                c.execute("ROLLBACK")
            raise
        finally:
            c.close()

    def audit(self, event: str, **detail: Any) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO audit(at, event, detail) VALUES (?,?,?)",
                      (utcnow().isoformat(), event, json.dumps(detail, default=str)[:4000]))

    def flag(self, key: str, default: str | None = None) -> str | None:
        with self.tx() as c:
            r = c.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

    def set_flag(self, key: str, value: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO flags(key, value, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE "
                      "SET value=excluded.value, updated_at=excluded.updated_at", (key, value, utcnow().isoformat()))

    def paused(self) -> bool:
        return self.flag("paused", "0") == "1"

    def count_events(self, event: str, since: datetime) -> int:
        with self.tx() as c:
            return int(c.execute("SELECT COUNT(*) FROM audit WHERE event=? AND at>=?",
                                 (event, iso(since))).fetchone()[0])
