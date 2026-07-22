"""Maintain the tradable ticker universe. Ticker extraction validates against
this table, so its quality directly bounds mention precision.

Default source is the SEC's public company_tickers file (free, no key). Falls
back to a small built-in seed list if the fetch fails so the pipeline still runs.
"""
from __future__ import annotations

import requests

from sgt.db.models import TickerUniverse
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert

_SEC_URL = "https://www.sec.gov/files/company_tickers.json"

_SEED = [
    ("AAPL", "Apple Inc."), ("MSFT", "Microsoft"), ("NVDA", "NVIDIA"),
    ("TSLA", "Tesla"), ("AMZN", "Amazon"), ("GME", "GameStop"),
    ("AMC", "AMC Entertainment"), ("SPY", "SPDR S&P 500 ETF"),
    ("QQQ", "Invesco QQQ"), ("META", "Meta Platforms"), ("GOOGL", "Alphabet"),
    ("PLTR", "Palantir"), ("F", "Ford"), ("T", "AT&T"), ("A", "Agilent"),
]


class UniverseLoader:
    name = "universe"

    def __init__(self, user_agent: str = "sgt research contact@example.com"):
        self.user_agent = user_agent

    def load(self) -> int:
        rows = self._fetch_sec() or [
            {"ticker": t, "name": n, "exchange": ""} for t, n in _SEED
        ]
        with get_session() as session:
            for r in rows:
                _upsert(session, TickerUniverse, {
                    "ticker": r["ticker"].upper(), "name": r["name"],
                    "exchange": r.get("exchange", ""), "active": True,
                }, ["ticker"])
        return len(rows)

    def _fetch_sec(self) -> list[dict] | None:
        try:
            resp = requests.get(_SEC_URL, headers={"User-Agent": self.user_agent}, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
        return [
            {"ticker": v["ticker"], "name": v.get("title", ""), "exchange": ""}
            for v in data.values()
            if v.get("ticker")
        ]


def load_universe_set() -> set[str]:
    """All active tickers, uppercased. Used by ticker extraction."""
    with get_session() as session:
        rows = session.query(TickerUniverse.ticker).filter(TickerUniverse.active).all()
    return {t[0].upper() for t in rows}
