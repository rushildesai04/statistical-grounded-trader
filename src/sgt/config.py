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

    @property
    def subreddit_list(self) -> list[str]:
        return [s.strip() for s in self.reddit_subreddits.split(",") if s.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
