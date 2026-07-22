"""Collect messages from StockTwits' public symbol streams.

StockTwits exposes a per-symbol message stream and, when the author tagged it,
a native Bullish/Bearish label — a strong, cheap sentiment prior we keep
alongside the model-derived score. Endpoint is rate-limited; we back off on 429.
"""
from __future__ import annotations

import time

import requests

from sgt.db.models import StockTwitsMessage
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert
from sgt.ingestion.base import Collector
from sgt.util import to_utc

_API = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"


class StockTwitsCollector(Collector):
    name = "stocktwits"

    def __init__(self, symbols: list[str], request_pause: float = 1.0, timeout: float = 15.0):
        self.symbols = symbols
        self.request_pause = request_pause
        self.timeout = timeout

    def collect(self) -> int:
        n = 0
        with get_session() as session:
            for symbol in self.symbols:
                n += self._collect_symbol(session, symbol)
                time.sleep(self.request_pause)  # be polite to the endpoint
        return n

    def _collect_symbol(self, session, symbol: str) -> int:
        try:
            resp = requests.get(_API.format(symbol=symbol), timeout=self.timeout)
        except requests.RequestException:
            return 0
        if resp.status_code == 429:
            time.sleep(60)
            return 0
        if resp.status_code != 200:
            return 0
        messages = resp.json().get("messages", [])
        n = 0
        for m in messages:
            entities = m.get("entities") or {}
            sentiment = (entities.get("sentiment") or {}).get("basic")  # Bullish|Bearish|None
            row = {
                "message_id": str(m["id"]),
                "symbol": symbol.upper(),
                "created_utc": to_utc(_parse_iso(m["created_at"])),
                "body": m.get("body", ""),
                "native_sentiment": sentiment,
                "likes": int((m.get("likes") or {}).get("total", 0)),
            }
            _upsert(session, StockTwitsMessage, row, ["message_id"])
            n += 1
        return n


def _parse_iso(s: str):
    from datetime import datetime

    # StockTwits returns e.g. "2024-01-02T15:04:05Z"
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
