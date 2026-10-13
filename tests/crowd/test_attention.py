"""Point-in-time crowd-attention feature tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sgt.crowd.attention import attention_features, platform_first_seen
from sgt.crowd.models import CrowdAlert
from sgt.db.models import Mention
from sgt.db.session import get_session

# 2026-10-09 18:30 UTC == 14:30 ET (regular session).
T = datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc)


def _alert(mid, idx, symbol, atype, ts, *, has_news=None, price=None):
    return CrowdAlert(
        channel="c", message_id=mid, idx=idx, symbol=symbol, alert_type=atype,
        price=price, posted_at=ts, has_news=has_news, headline=None,
    )


def _mention(source_type, sid, ticker, ts, author):
    return Mention(
        source_type=source_type, source_id=sid, ticker=ticker, created_utc=ts,
        author_hash=author, weight=1.0, sentiment_label="neutral",
        sentiment_scores={}, model_version="t",
    )


def _add(objs):
    with get_session() as session:
        for o in objs:
            session.add(o)


def test_future_events_excluded():
    _add([
        _alert(1, 0, "YMAT", "new_high", T - timedelta(minutes=10)),
        _alert(2, 0, "YMAT", "new_high", T + timedelta(minutes=10)),  # future, must be ignored
        _mention("stocktwits", "s1", "YMAT", T + timedelta(minutes=5), "a"),  # future
    ])
    with get_session() as session:
        feats = attention_features(session, "YMAT", T)
    assert feats["tg_new_high_1h"] == 1.0
    assert feats["tg_alert_count_1h"] == 1.0
    assert feats["mention_count_1h"] == 1.0  # only the past alert


def test_event_exactly_at_t_excluded():
    _add([_alert(1, 0, "YMAT", "new_high", T)])
    with get_session() as session:
        feats = attention_features(session, "YMAT", T)
    assert feats["tg_alert_count_1h"] == 0.0


def test_unique_authors_from_mentions():
    _add([
        _mention("stocktwits", "s1", "YMAT", T - timedelta(minutes=5), "alice"),
        _mention("stocktwits", "s2", "YMAT", T - timedelta(minutes=6), "alice"),
        _mention("reddit_post", "r1", "YMAT", T - timedelta(minutes=7), "bob"),
    ])
    with get_session() as session:
        feats = attention_features(session, "YMAT", T)
    assert feats["unique_authors_1h"] == 2.0


def test_platform_stage_ordering():
    # telegram only -> stage 1
    _add([_alert(1, 0, "AAA", "new_high", T - timedelta(minutes=5))])
    with get_session() as session:
        assert attention_features(session, "AAA", T)["platform_stage"] == 1.0

    # add stocktwits -> stage 2
    _add([_mention("stocktwits", "s1", "AAA", T - timedelta(minutes=4), "x")])
    with get_session() as session:
        assert attention_features(session, "AAA", T)["platform_stage"] == 2.0

    # add news -> stage 4 (highest wins)
    _add([_mention("news", "n1", "AAA", T - timedelta(minutes=3), "")])
    with get_session() as session:
        assert attention_features(session, "AAA", T)["platform_stage"] == 4.0


def test_no_platform_stage_zero():
    with get_session() as session:
        assert attention_features(session, "ZZZ", T)["platform_stage"] == 0.0


def test_consecutive_new_high_streak():
    _add([
        _alert(1, 0, "YMAT", "new_high", T - timedelta(minutes=30)),
        _alert(2, 0, "YMAT", "halted", T - timedelta(minutes=20)),
        _alert(3, 0, "YMAT", "new_high", T - timedelta(minutes=10)),
        _alert(4, 0, "YMAT", "new_high", T - timedelta(minutes=5)),
    ])
    with get_session() as session:
        feats = attention_features(session, "YMAT", T)
    assert feats["consecutive_new_high_alerts"] == 2.0


def test_news_flags():
    _add([
        _alert(1, 0, "YMAT", "news", T - timedelta(minutes=10), has_news=False),
    ])
    with get_session() as session:
        feats = attention_features(session, "YMAT", T)
    assert feats["no_news_flag"] == 1.0
    assert feats["has_news_today"] == 0.0

    _add([_alert(2, 0, "YMAT", "news", T - timedelta(minutes=5), has_news=True)])
    with get_session() as session:
        feats = attention_features(session, "YMAT", T)
    assert feats["has_news_today"] == 1.0
    assert feats["no_news_flag"] == 0.0


def test_minutes_since_first_alert_today():
    _add([_alert(1, 0, "YMAT", "new_high", T - timedelta(minutes=45))])
    with get_session() as session:
        feats = attention_features(session, "YMAT", T)
    assert feats["minutes_since_first_alert_today"] == 45.0


def test_minutes_since_no_alert_sentinel():
    with get_session() as session:
        feats = attention_features(session, "YMAT", T)
    assert feats["minutes_since_first_alert_today"] == -1.0


def test_velocity_zscore_positive_on_spike():
    # No baseline activity, then a burst in the current window -> positive z.
    _add([
        _alert(i, 0, "YMAT", "new_high", T - timedelta(minutes=m))
        for i, m in enumerate([5, 10, 15, 20, 25, 30, 35, 40], start=1)
    ])
    with get_session() as session:
        feats = attention_features(session, "YMAT", T)
    assert feats["mention_velocity_z"] > 0.0


def test_platform_first_seen_respects_before():
    _add([
        _alert(1, 0, "YMAT", "new_high", T - timedelta(minutes=10)),
        _alert(2, 0, "YMAT", "new_high", T + timedelta(minutes=10)),
    ])
    with get_session() as session:
        seen = platform_first_seen(session, "YMAT", T.astimezone().date(), before=T)
    # ET session date for T:
    with get_session() as session:
        from zoneinfo import ZoneInfo
        sd = T.astimezone(ZoneInfo("America/New_York")).date()
        seen = platform_first_seen(session, "YMAT", sd, before=T)
    assert seen["telegram"] == (T - timedelta(minutes=10))
