"""Point-in-time crowd-attention features for one symbol at time ``t``.

Every function here is a pure query over a SQLAlchemy ``Session`` and obeys the
platform's leakage firewall: only rows with event time **strictly before** ``t``
may contribute (docs/IMPLEMENTATION_PLAN.md §4.4, §12). Telegram alerts come from
``crowd_alerts``; cross-platform mentions come from the shared ``mentions`` table.

Returned feature dicts use fixed keys so the vector is stable across calls (missing
counts are 0.0; "minutes since" is -1.0 when the event has not happened yet).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sgt.crowd.telegram_parser import ALERT_TYPES
from sgt.db.models import Mention
from sgt.crowd.models import CrowdAlert
from sgt.util import to_utc

ET = ZoneInfo("America/New_York")

# Diffusion ladder: later platforms imply the move has spread further.
_PLATFORM_STAGE = {"telegram": 1, "stocktwits": 2, "reddit": 3, "news": 4}
# mentions.source_type -> platform label
_SOURCE_PLATFORM = {
    "stocktwits": "stocktwits",
    "reddit_post": "reddit",
    "reddit_comment": "reddit",
    "news": "news",
}
# Alert types that count toward telegram per-type features (news handled separately).
_TG_COUNT_TYPES = tuple(a for a in ALERT_TYPES if a != "news")


def _require_utc(t: datetime) -> None:
    if t.tzinfo is None or t.utcoffset() != timezone.utc.utcoffset(None):
        raise ValueError(f"t must be tz-aware UTC, got {t!r}")


def _session_bounds_utc(t: datetime) -> tuple[datetime, date]:
    """Return (ET-midnight-of-t in UTC, ET session date)."""
    et = t.astimezone(ET)
    session_date = et.date()
    midnight_et = datetime.combine(session_date, datetime.min.time(), tzinfo=ET)
    return midnight_et.astimezone(timezone.utc), session_date


def _count_events(session: Session, symbol: str, start: datetime, end: datetime) -> int:
    """Combined mentions + crowd_alerts whose event time is in [start, end)."""
    m = session.execute(
        select(func.count()).select_from(Mention).where(
            Mention.ticker == symbol,
            Mention.created_utc >= start,
            Mention.created_utc < end,
        )
    ).scalar_one()
    a = session.execute(
        select(func.count()).select_from(CrowdAlert).where(
            CrowdAlert.symbol == symbol,
            CrowdAlert.posted_at >= start,
            CrowdAlert.posted_at < end,
        )
    ).scalar_one()
    return int(m) + int(a)


def platform_first_seen(
    session: Session,
    symbol: str,
    session_date: date,
    *,
    before: datetime | None = None,
) -> dict[str, datetime | None]:
    """Earliest event time per platform for ``symbol`` on the ET ``session_date``.

    Only events strictly before ``before`` (when given) are considered. Platforms
    with no qualifying event map to ``None``.
    """
    day_start = datetime.combine(session_date, datetime.min.time(), tzinfo=ET).astimezone(
        timezone.utc
    )
    day_end = day_start + timedelta(days=1)
    upper = day_end if before is None else min(day_end, to_utc(before))

    out: dict[str, datetime | None] = {p: None for p in _PLATFORM_STAGE}

    tg = session.execute(
        select(func.min(CrowdAlert.posted_at)).where(
            CrowdAlert.symbol == symbol,
            CrowdAlert.posted_at >= day_start,
            CrowdAlert.posted_at < upper,
        )
    ).scalar()
    out["telegram"] = to_utc(tg) if tg is not None else None

    rows = session.execute(
        select(Mention.source_type, func.min(Mention.created_utc)).where(
            Mention.ticker == symbol,
            Mention.created_utc >= day_start,
            Mention.created_utc < upper,
        ).group_by(Mention.source_type)
    ).all()
    for source_type, first in rows:
        platform = _SOURCE_PLATFORM.get(source_type)
        if platform is None or first is None:
            continue
        cur = out[platform]
        first = to_utc(first)
        out[platform] = first if cur is None or first < cur else cur
    return out


def attention_features(
    session: Session,
    symbol: str,
    t: datetime,
    *,
    mentions_window: timedelta = timedelta(hours=1),
    baseline_days: int = 5,
) -> dict[str, float]:
    """Point-in-time crowd features for ``symbol`` at ``t`` (tz-aware UTC)."""
    _require_utc(t)
    if mentions_window <= timedelta(0):
        raise ValueError("mentions_window must be positive")
    if baseline_days <= 0:
        raise ValueError("baseline_days must be positive")

    symbol = symbol.upper()
    window_start = t - mentions_window
    feats: dict[str, float] = {}

    # --- mention velocity z-score vs the symbol's own trailing baseline -----
    current = _count_events(session, symbol, window_start, t)
    win_s = mentions_window.total_seconds()
    n_buckets = int((baseline_days * 86400) // win_s)
    baseline_counts: list[int] = []
    for k in range(1, n_buckets + 1):
        b_end = t - timedelta(seconds=win_s * k)
        b_start = b_end - mentions_window
        baseline_counts.append(_count_events(session, symbol, b_start, b_end))
    if baseline_counts:
        mean = sum(baseline_counts) / len(baseline_counts)
        var = sum((c - mean) ** 2 for c in baseline_counts) / len(baseline_counts)
        std = var ** 0.5
        if std > 0:
            z = (current - mean) / std
        elif current > mean:
            # Flat (zero-variance) baseline: a burst still signals. Degrade the
            # z-score to the excess event count so first-appearance spikes on
            # low-float names are not silently zeroed out.
            z = float(current - mean)
        else:
            z = 0.0
    else:
        z = 0.0
    feats["mention_count_1h"] = float(current)
    feats["mention_velocity_z"] = float(z)

    # --- unique authors in the window (mentions carry author_hash) ----------
    uniq = session.execute(
        select(func.count(func.distinct(Mention.author_hash))).where(
            Mention.ticker == symbol,
            Mention.created_utc >= window_start,
            Mention.created_utc < t,
            Mention.author_hash != "",
        )
    ).scalar_one()
    feats["unique_authors_1h"] = float(uniq)

    # --- telegram alert counts by type in the window ------------------------
    rows = session.execute(
        select(CrowdAlert.alert_type, func.count()).where(
            CrowdAlert.symbol == symbol,
            CrowdAlert.posted_at >= window_start,
            CrowdAlert.posted_at < t,
        ).group_by(CrowdAlert.alert_type)
    ).all()
    by_type = {a_type: int(cnt) for a_type, cnt in rows}
    total_tg = 0
    for a_type in _TG_COUNT_TYPES:
        c = by_type.get(a_type, 0)
        feats[f"tg_{a_type}_1h"] = float(c)
        total_tg += c
    feats["tg_alert_count_1h"] = float(total_tg)

    # --- today's alert-stream features (ET session date) --------------------
    day_start_utc, session_date = _session_bounds_utc(t)
    today = session.execute(
        select(CrowdAlert.alert_type, CrowdAlert.has_news, CrowdAlert.posted_at).where(
            CrowdAlert.symbol == symbol,
            CrowdAlert.posted_at >= day_start_utc,
            CrowdAlert.posted_at < t,
        ).order_by(CrowdAlert.posted_at.asc(), CrowdAlert.idx.asc())
    ).all()

    if today:
        first_ts = to_utc(today[0].posted_at)
        feats["minutes_since_first_alert_today"] = (t - first_ts).total_seconds() / 60.0
    else:
        feats["minutes_since_first_alert_today"] = -1.0

    # trailing run of consecutive new_high alerts
    streak = 0
    for alert_type, _has_news, _ts in today:
        if alert_type == "new_high":
            streak += 1
        else:
            streak = 0
    feats["consecutive_new_high_alerts"] = float(streak)

    has_news_today = any(at == "news" and hn is True for at, hn, _ in today)
    had_no_news = any(at == "news" and hn is False for at, hn, _ in today)
    feats["has_news_today"] = 1.0 if has_news_today else 0.0
    feats["no_news_flag"] = 1.0 if (had_no_news and not has_news_today) else 0.0

    # --- platform diffusion stage ------------------------------------------
    first_seen = platform_first_seen(session, symbol, session_date, before=t)
    stage = 0
    for platform, seen in first_seen.items():
        if seen is not None:
            stage = max(stage, _PLATFORM_STAGE[platform])
    feats["platform_stage"] = float(stage)

    return feats
