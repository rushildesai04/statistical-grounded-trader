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
            cur.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            logging.getLogger(__name__).warning("could not enable SQLite WAL yet: %s", exc)
    finally:
        cur.close()


def init_db() -> None:
    """Create all tables. Idempotent. For prod, prefer versioned migrations."""
    import sgt.db.registry  # noqa: F401  registers intraday ORM modules on Base

    Base.metadata.create_all(get_engine())


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
