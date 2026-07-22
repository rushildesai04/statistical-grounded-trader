"""Per-ticker news headlines via yfinance. Headlines feed the same sentiment
pipeline as social text (source_type='news'), giving a non-retail signal to
contrast with WSB/StockTwits."""
from __future__ import annotations

from sgt.db.models import NewsArticle
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert
from sgt.ingestion.base import Collector
from sgt.util import epoch_to_utc


class NewsCollector(Collector):
    name = "news"

    def __init__(self, tickers: list[str]):
        self.tickers = tickers

    def collect(self) -> int:
        import yfinance as yf

        n = 0
        with get_session() as session:
            for ticker in self.tickers:
                try:
                    items = yf.Ticker(ticker).news or []
                except Exception:
                    continue
                for it in items:
                    uuid = it.get("uuid") or it.get("id")
                    ts = it.get("providerPublishTime")
                    if not uuid or not ts:
                        continue
                    _upsert(session, NewsArticle, {
                        "article_id": str(uuid),
                        "ticker": ticker.upper(),
                        "published_utc": epoch_to_utc(float(ts)),
                        "title": it.get("title", ""),
                        "publisher": it.get("publisher", ""),
                        "link": it.get("link", ""),
                    }, ["article_id"])
                    n += 1
        return n
