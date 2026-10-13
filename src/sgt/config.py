"""Central configuration loaded from environment / .env (prefix SGT_)."""
from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SGT_", env_file=".env", extra="ignore"
    )

    database_url: str = "sqlite:///sgt.db"

    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "sgt:statistical-grounded-trader:0.1.0"
    reddit_subreddits: str = "wallstreetbets,stocks,investing,options,StockMarket"

    author_hash_salt: str = "change-me"

    sentiment_model: str = "ProsusAI/finbert"
    sentiment_backend: str = "auto"  # auto | finbert | lexicon

    cost_bps: float = 10.0

    # --- Intraday track (docs/IMPLEMENTATION_PLAN.md). Secrets are NOT settings:
    # broker keys / tokens come from sgt.core.secrets (keychain or SGT_SECRET_*).
    data_dir: str = "data"                       # Parquet market store root
    event_log_dir: str = "data/eventlog"         # append-only JSONL audit logs
    alpaca_data_url: str = "https://data.alpaca.markets"
    alpaca_paper_url: str = "https://paper-api.alpaca.markets"
    alpaca_live_url: str = "https://api.alpaca.markets"
    alpaca_feed: str = "sip"                     # sip | iex | delayed_sip
    http_timeout_s: float = 10.0
    sec_user_agent: str = "statistical-grounded-trader research contact@example.com"
    telegram_channels: str = ""                  # comma-separated channel usernames/ids
    telegram_session_path: str = "data/telegram.session"
    control_plane_host: str = "127.0.0.1"
    alert_webhook_url: str = ""                  # optional push target for alert-only adapter

    @property
    def telegram_channel_list(self) -> list[str]:
        return [s.strip() for s in self.telegram_channels.split(",") if s.strip()]

    @property
    def subreddit_list(self) -> list[str]:
        return [s.strip() for s in self.reddit_subreddits.split(",") if s.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
