"""SQLite concurrency settings: WAL + busy timeout (2026-10-18 'database is locked')."""
from __future__ import annotations

from sqlalchemy import text

from sgt.db.session import get_engine


def test_sqlite_uses_wal_and_busy_timeout():
    eng = get_engine()
    if not str(eng.url).startswith("sqlite"):
        return
    with eng.connect() as c:
        assert c.execute(text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert int(c.execute(text("PRAGMA busy_timeout")).scalar()) >= 30000
