"""Collector + provider interfaces. Concrete impls are swappable via these."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


class Collector(ABC):
    """A source that fetches raw records and upserts them into the DB."""

    name: str = "collector"

    @abstractmethod
    def collect(self) -> int:
        """Fetch + persist. Returns number of new/updated rows. Must be idempotent."""
        raise NotImplementedError


@dataclass
class Bar:
    ticker: str
    date: datetime
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: float


class MarketDataProvider(ABC):
    """Abstraction over the market-data vendor so yfinance can be swapped for
    Polygon/Alpaca without touching downstream code."""

    name: str = "provider"

    @abstractmethod
    def daily_bars(self, ticker: str, start: datetime, end: datetime) -> list[Bar]:
        raise NotImplementedError
