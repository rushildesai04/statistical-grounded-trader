"""ORM tables owned by sgt.intraday. Registered on the shared Base via sgt.db.registry.

All timestamps are timezone-aware UTC. Session dates are stored as ISO strings
(``YYYY-MM-DD``) so they compare lexicographically and never carry a spurious
timezone. Rows are idempotent-upsert; nothing is mutated in place except the
tracking window, which legitimately extends as a symbol is re-discovered.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from sgt.db.models import Base


class TrackedSymbolRow(Base):
    """One symbol the daily accumulation job is keeping intraday history for.

    A symbol becomes tracked when a screener surfaces it (or it is on the static
    watchlist) and stays ACTIVE until ``active_until_session`` (the Nth trading
    session after its most recent discovery). Session columns are ISO date strings.
    """

    __tablename__ = "tracked_symbols"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    first_seen_session: Mapped[str] = mapped_column(String(10))
    last_seen_session: Mapped[str] = mapped_column(String(10), index=True)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    active_until_session: Mapped[str] = mapped_column(String(10), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
