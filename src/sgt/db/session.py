"""Engine + session factory. Import `get_session` everywhere; never build engines ad hoc."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from sgt.config import get_settings
from sgt.db.models import Base

_engine = None
_Session: sessionmaker | None = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = get_settings().database_url
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, future=True, connect_args=connect_args)
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db() -> None:
    """Create all tables. Idempotent. For prod, prefer versioned migrations."""
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
