"""Engine + session factory. Import `get_session` everywhere; never build engines ad hoc."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from sgt.config import get_settings
from sgt.db.models import Base

_engine = None
_Session: sessionmaker | None = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = get_settings().database_url
        is_sqlite = url.startswith("sqlite")
        # SQLite is shared by the live runner, control plane, daily job and trainers.
        # A 30 s busy timeout + WAL (concurrent readers, one writer) replaces the default
        # 5 s lock wait that crashed a spread-measurement run alongside training
        # (2026-10-18: "database is locked").
        connect_args = {"check_same_thread": False, "timeout": 30.0} if is_sqlite else {}
        _engine = create_engine(url, future=True, connect_args=connect_args)
        if is_sqlite:
            event.listen(_engine, "connect", _sqlite_pragmas)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    import logging
    import sqlite3

    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA busy_timeout=30000")  # first, so the WAL switch can wait too
        try:
            # WAL is a persistent property of the file; switching needs a moment of
            # exclusive access. If another process holds the DB, keep the current mode
            # (busy_timeout still applies) and retry on the next connection.
            row = cur.execute("PRAGMA journal_mode=WAL").fetchone()
        except sqlite3.OperationalError as exc:
            logging.getLogger(__name__).warning("could not enable SQLite WAL yet: %s", exc)
        else:
            # F5 (2026-10-18): SQLite does NOT raise when it cannot switch journal modes --
            # it silently returns the mode still in effect. Read it back and warn so a
            # degraded (non-WAL) connection under contention is visible, not silent.
            mode = (str(row[0]).lower() if row else "")
            if mode != "wal":
                logging.getLogger(__name__).warning(
                    "SQLite WAL not enabled (journal_mode=%s); another process may hold the "
                    "DB. busy_timeout still applies; retrying on the next connection.",
                    mode or "unknown")
    finally:
        cur.close()


def init_db() -> None:
    """Create all tables. Idempotent. For prod, prefer versioned migrations."""
    import sgt.db.registry  # noqa: F401  registers intraday ORM modules on Base

    engine = get_engine()
    Base.metadata.create_all(engine)
    _add_missing_nullable_columns(engine)


def _add_missing_nullable_columns(engine) -> None:
    """Additive-only schema sync (2026-10-29): create_all never alters an existing
    table, so a NULLABLE column added to a model later is appended here. Never drops,
    renames or retypes anything; non-nullable additions still need a real migration."""
    from sqlalchemy import inspect, text

    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have or not col.nullable or col.primary_key:
                    continue
                ddl = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {ddl}'))


@contextmanager
def get_session() -> Iterator[Session]:
    if _Session is None:
        get_engine()
    assert _Session is not None
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
