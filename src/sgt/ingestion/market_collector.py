"""Market OHLCV ingestion behind a swappable provider.

Also fetches ^VIX as a market-regime feature source. yfinance is the MVP
provider; implement another MarketDataProvider to switch vendors.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sgt.db.models import OHLCV
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert
from sgt.ingestion.base import Bar, Collector, MarketDataProvider
from sgt.util import to_utc, utcnow

# ^VIX gives volatility regime; carried as a pseudo-ticker in ohlcv.
REGIME_TICKERS = ["^VIX"]


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"

    def daily_bars(self, ticker: str, start: datetime, end: datetime) -> list[Bar]:
        import yfinance as yf

        df = yf.download(
            ticker, start=start.date(), end=end.date(),
            auto_adjust=False, progress=False, threads=False,
        )
        if df is None or df.empty:
            return []
        bars: list[Bar] = []
        for idx, r in df.iterrows():
            def val(col):  # yfinance may return single- or multi-index columns
                v = r[col]
                return float(v.iloc[0] if hasattr(v, "iloc") else v)

            bars.append(Bar(
                ticker=ticker,
                date=to_utc(idx.to_pydatetime()),
                open=val("Open"), high=val("High"), low=val("Low"),
                close=val("Close"),
                adj_close=val("Adj Close") if "Adj Close" in df.columns else val("Close"),
                volume=val("Volume"),
            ))
        return bars


class MarketCollector(Collector):
    name = "market"

    def __init__(self, tickers: list[str], lookback_days: int = 400,
                 provider: MarketDataProvider | None = None):
        self.tickers = list(dict.fromkeys(tickers + REGIME_TICKERS))
        self.lookback_days = lookback_days
        self.provider = provider or YFinanceProvider()

    def collect(self) -> int:
        end = utcnow()
        start = end - timedelta(days=self.lookback_days)
        n = 0
        with get_session() as session:
            for ticker in self.tickers:
                for bar in self.provider.daily_bars(ticker, start, end):
                    _upsert(session, OHLCV, {
                        "ticker": bar.ticker, "date": bar.date,
                        "open": bar.open, "high": bar.high, "low": bar.low,
                        "close": bar.close, "adj_close": bar.adj_close,
                        "volume": bar.volume, "provider": self.provider.name,
                    }, ["ticker", "date"])
                    n += 1
        return n
