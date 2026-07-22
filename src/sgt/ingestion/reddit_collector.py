"""Collect submissions + comments from configured subreddits via the official
Reddit API (PRAW). Idempotent upserts keyed on Reddit's stable ids.

Requires a registered Reddit app (client id/secret/user-agent). Respects the
API rate limit — PRAW handles backoff internally.
"""
from __future__ import annotations

from sgt.config import get_settings
from sgt.db.models import RedditComment, RedditPost
from sgt.db.session import get_session
from sgt.db.upsert import upsert as _upsert
from sgt.ingestion.base import Collector
from sgt.util import epoch_to_utc, hash_author


class RedditCollector(Collector):
    name = "reddit"

    def __init__(self, subreddits: list[str] | None = None, post_limit: int = 200,
                 comments_per_post: int = 50):
        s = get_settings()
        self.subreddits = subreddits or s.subreddit_list
        self.post_limit = post_limit
        self.comments_per_post = comments_per_post

    def _client(self):
        import praw  # imported lazily so the package loads without creds/deps

        s = get_settings()
        if not s.reddit_client_id or not s.reddit_client_secret:
            raise RuntimeError(
                "Reddit API credentials missing. Set SGT_REDDIT_CLIENT_ID / "
                "SGT_REDDIT_CLIENT_SECRET (register an app at reddit.com/prefs/apps)."
            )
        return praw.Reddit(
            client_id=s.reddit_client_id,
            client_secret=s.reddit_client_secret,
            user_agent=s.reddit_user_agent,
            check_for_async=False,
        )

    def collect(self) -> int:
        reddit = self._client()
        n = 0
        with get_session() as session:
            for sub_name in self.subreddits:
                sub = reddit.subreddit(sub_name)
                for post in sub.new(limit=self.post_limit):
                    n += self._upsert_post(session, sub_name, post)
                    post.comments.replace_more(limit=0)
                    for c in post.comments.list()[: self.comments_per_post]:
                        n += self._upsert_comment(session, sub_name, post.id, c)
        return n

    @staticmethod
    def _upsert_post(session, sub_name, post) -> int:
        row = {
            "reddit_id": post.id,
            "subreddit": sub_name,
            "created_utc": epoch_to_utc(post.created_utc),
            "author_hash": hash_author(getattr(post.author, "name", None)),
            "score": int(post.score or 0),
            "title": post.title or "",
            "body": post.selftext or "",
            "permalink": post.permalink or "",
        }
        _upsert(session, RedditPost, row, ["reddit_id"])
        return 1

    @staticmethod
    def _upsert_comment(session, sub_name, post_id, c) -> int:
        row = {
            "reddit_id": c.id,
            "post_id": post_id,
            "subreddit": sub_name,
            "created_utc": epoch_to_utc(c.created_utc),
            "author_hash": hash_author(getattr(c.author, "name", None)),
            "score": int(c.score or 0),
            "body": c.body or "",
        }
        _upsert(session, RedditComment, row, ["reddit_id"])
        return 1
