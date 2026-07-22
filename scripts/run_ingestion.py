"""Collect all sources: Reddit + StockTwits + market + news.

Market/StockTwits/news are keyed to tickers currently trending on WSB (by recent
mention volume) intersected with the universe, plus a core watchlist — so we do
not hammer every provider for every ticker.
"""
from __future__ import annotations

from sgt.db.models import Mention
from sgt.db.session import get_session, init_db
from sgt.ingestion.market_collector import MarketCollector
from sgt.ingestion.news_collector import NewsCollector
from sgt.ingestion.reddit_collector import RedditCollector
from sgt.ingestion.stocktwits_collector import StockTwitsCollector

CORE_WATCHLIST = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GME", "AMC", "SPY", "QQQ"]


def trending_tickers(limit: int = 50) -> list[str]:
    with get_session() as session:
        rows = session.query(Mention.ticker).all()
    counts: dict[str, int] = {}
    for (t,) in rows:
        counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts, key=counts.get, reverse=True)[:limit]
    return list(dict.fromkeys(CORE_WATCHLIST + ranked))


if __name__ == "__main__":
    init_db()

    try:
        n = RedditCollector().collect()
        print(f"Reddit: {n} rows")
    except RuntimeError as e:
        print(f"Reddit skipped: {e}")

    tickers = trending_tickers()
    print(f"Market/news/stocktwits target tickers: {len(tickers)}")

    print(f"StockTwits: {StockTwitsCollector(tickers).collect()} rows")
    print(f"Market: {MarketCollector(tickers).collect()} bars")
    print(f"News: {NewsCollector(tickers).collect()} articles")
