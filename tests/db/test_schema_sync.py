"""init_db appends nullable model columns missing from an existing table (additive only)."""
from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from sgt.db import session as db_session
from sgt.db.models import Base


def test_adds_missing_nullable_column_and_keeps_rows(tmp_path):
    import sgt.db.registry  # noqa: F401

    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:  # the pre-2026-10-29 telegram_messages layout
        conn.execute(text(
            "CREATE TABLE telegram_messages (channel VARCHAR(128) NOT NULL, message_id INTEGER NOT NULL,"
            " posted_at DATETIME NOT NULL, raw_text TEXT NOT NULL, ingested_at DATETIME NOT NULL,"
            " parse_status VARCHAR(16) NOT NULL, PRIMARY KEY (channel, message_id))"))
        conn.execute(text("INSERT INTO telegram_messages VALUES ('c', 1, '2026-01-01', 'x', '2026-01-01', 'parsed')"))
    Base.metadata.create_all(engine)
    db_session._add_missing_nullable_columns(engine)
    db_session._add_missing_nullable_columns(engine)  # idempotent

    cols = {c["name"] for c in inspect(engine).get_columns("telegram_messages")}
    assert "edited_at" in cols
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*), max(edited_at) FROM telegram_messages")).one() == (1, None)
