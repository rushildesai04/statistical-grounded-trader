"""Small shared helpers."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sgt.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(dt: datetime) -> datetime:
    """Force a datetime to tz-aware UTC. Naive datetimes are assumed UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def epoch_to_utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def hash_author(username: str | None) -> str:
    """Salted hash of a public username so we never store it in the clear."""
    if not username:
        return ""
    salt = get_settings().author_hash_salt.encode()
    return hashlib.sha256(salt + username.encode()).hexdigest()[:32]
