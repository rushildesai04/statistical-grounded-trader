"""ORM table for TradingView alert-webhook deliveries (sgt.crowd).

TradingView's only supported outbound path is alert webhooks: on an alert it POSTs
a user-authored JSON body to a public URL. We persist one strictly validated row
per delivery, keyed by (alert_name, ticker, bar time) so an alert that re-fires for
the same bar (TradingView may retry on timeout) upserts in place instead of
duplicating. All timestamps are timezone-aware UTC. Registered on the shared Base
via ``sgt.db.registry``.

The shared webhook secret is NEVER stored here — it is verified at the boundary and
discarded (see ``sgt.api.webhook_app``).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from sgt.db.models import Base


class TradingViewAlert(Base):
    """One TradingView alert-webhook delivery, strictly validated before storage.

    ``ts`` is the alert's bar time (the ``{{time}}`` placeholder, UTC). The primary
    key (alert_name, ticker, ts) makes storage idempotent per bar. ``indicators_json``
    holds the optional ``{name: float}`` map (TradingView's own EMA/MACD/RSI/VWAP
    values) as a JSON object string; ``None`` when the alert carried no indicators.
    """

    __tablename__ = "tradingview_alerts"

    alert_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(16), primary_key=True, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True, index=True)
    exchange: Mapped[str | None] = mapped_column(String(32), nullable=True)
    interval: Mapped[str | None] = mapped_column(String(16), nullable=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)
    indicators_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
