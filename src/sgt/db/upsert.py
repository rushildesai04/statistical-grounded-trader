"""Portable idempotent upsert used by all collectors."""
from __future__ import annotations

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert


def upsert(session, model, row: dict, pk: list[str]) -> None:
    """Insert `row`, updating non-PK columns on conflict. Supports SQLite +
    Postgres natively; other dialects fall back to ORM merge."""
    dialect = session.get_bind().dialect.name
    if dialect == "sqlite":
        stmt = sqlite_insert(model).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=pk, set_={k: stmt.excluded[k] for k in row if k not in pk}
        )
        session.execute(stmt)
    elif dialect == "postgresql":
        stmt = pg_insert(model).values(**row)
        stmt = stmt.on_conflict_do_update(
            index_elements=pk, set_={k: stmt.excluded[k] for k in row if k not in pk}
        )
        session.execute(stmt)
    else:
        session.merge(model(**row))
