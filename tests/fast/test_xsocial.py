from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from sgt.fast import fade, xsocial

AT = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
Z = "%Y-%m-%dT%H:%M:%S.000Z"


def post(minutes_ago, author, likes):
    return {"created_at": (AT - timedelta(minutes=minutes_ago)).strftime(Z), "author_id": author,
            "public_metrics": {"like_count": likes, "retweet_count": 1, "reply_count": 0, "quote_count": 0}}


def bucket(minutes_ago, n):
    end = AT - timedelta(minutes=minutes_ago)
    return {"start": (end - timedelta(minutes=1)).strftime(Z), "end": end.strftime(Z), "tweet_count": n}


def test_counts_split_last_hour_and_last_15_min():
    c = xsocial.counts({"data": [bucket(50, 7), bucket(20, 3), bucket(10, 2), bucket(0, 1), "junk",
                                 {"end": bucket(5, 0)["end"], "tweet_count": "9"}]}, AT)
    assert c == {"x_n60": 13, "x_n15": 3}
    assert xsocial.counts({"errors": [{"title": "x"}]}, AT) is None                # not a counts answer -> unknown


def test_sample_is_point_in_time():
    s = xsocial.sample({"data": [post(5, "a", 10), post(30, "a", 2), post(-2, "c", 99), "junk"]}, AT)
    assert s == {"x_k": 2, "x_authors": 1, "x_eng": 14}


def _client(seen, counts_payload, search_status=200):
    def handler(req):
        seen.append((req.url.path, dict(req.url.params)))
        if req.url.path.endswith("/counts/recent"):
            return httpx.Response(200, json=counts_payload)
        return httpx.Response(search_status, json={"data": [post(3, "a", 5), post(4, "b", 1)]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _reset(monkeypatch, key="tok"):
    monkeypatch.setenv("SGT_X_ENABLED", "1")
    xsocial._CALLS.clear()
    xsocial._CACHE.clear()
    monkeypatch.setattr(xsocial, "get_secret", lambda name, required=True: key)


def test_dormant_without_key_and_capped(monkeypatch):
    _reset(monkeypatch, key=None)
    assert xsocial.lookup("RAYA", AT) is None and xsocial._CALLS == {}          # no key: no call, nothing counted
    _reset(monkeypatch)
    seen = []
    c = _client(seen, {"data": [bucket(3, 2)]})
    xsocial._CALLS[AT.date().isoformat()] = xsocial.DAILY_CAP
    assert xsocial.lookup("RAYA", AT, client=c) is None and seen == []          # cap reached: no call
    xsocial._CALLS.clear()
    assert xsocial.lookup("bad sym", AT, client=c) is None and seen == []


def test_counts_first_then_small_sample(monkeypatch):
    _reset(monkeypatch)
    seen = []
    r = xsocial.lookup("RAYA", AT, client=_client(seen, {"data": [bucket(30, 40), bucket(3, 2)]}))
    assert r == {"x_n60": 42, "x_n15": 2, "x_k": 2, "x_authors": 2, "x_eng": 8}
    assert [p for p, _ in seen] == ["/2/tweets/counts/recent", "/2/tweets/search/recent"]
    assert seen[0][1]["end_time"] == seen[1][1]["end_time"] == "2026-11-09T14:59:45Z"        # point in time
    assert seen[0][1]["granularity"] == "minute" and seen[0][1]["query"] == "$RAYA -is:retweet"
    assert seen[1][1]["max_results"] == str(xsocial.SAMPLE) == "10"          # billed per post: never a 100 page
    assert xsocial._CALLS == {AT.date().isoformat(): 1}


def test_no_posts_skips_the_paid_sample(monkeypatch):
    _reset(monkeypatch)
    seen = []
    r = xsocial.lookup("RAYA", AT, client=_client(seen, {"data": [bucket(3, 0)], "meta": {"total_tweet_count": 0}}))
    assert r == {"x_n60": 0, "x_n15": 0, "x_k": 0, "x_authors": 0, "x_eng": 0}
    assert len(seen) == 1                                                        # counts only


def test_sample_failure_keeps_counts_and_counts_failure_is_unknown(monkeypatch):
    _reset(monkeypatch)
    r = xsocial.lookup("RAYA", AT, client=_client([], {"data": [bucket(3, 5)]}, search_status=402))
    assert r["x_n60"] == 5 and r["x_authors"] is None and r["x_eng"] is None
    _reset(monkeypatch)
    assert xsocial.lookup("RAYA", AT, client=_client([], {"title": "Payment Required"})) is None


def test_cache_reuses_answer_within_ttl(monkeypatch):
    _reset(monkeypatch)
    seen = []
    c = _client(seen, {"data": [bucket(3, 2)]})
    a = xsocial.lookup("RAYA", AT, client=c)
    a["x_n60"] = -1                                                              # callers cannot corrupt the cache
    assert xsocial.lookup("RAYA", AT + timedelta(minutes=9), client=c)["x_n60"] == 2 and len(seen) == 2
    assert xsocial._CALLS == {AT.date().isoformat(): 1}                         # cache hit is free
    xsocial.lookup("RAYA", AT + timedelta(minutes=11), client=c)
    assert len(seen) == 4                                                        # expired: one new paid lookup


def test_entry_text_line():
    base = {"news": {"news_any": 0, "news_own": 0, "news_earn": 0}}
    ctx = {**base, "x": {"x_n60": 42, "x_n15": 9, "x_k": 10, "x_authors": 7, "x_eng": 1234}}
    assert "𝕏 42 posts last hour; newest 10: 7 accounts, 1,234 likes/reposts" in fade.ask_text("ABCD", "news", 2.0,
                                                                                                  5000, ctx, "")
    ctx = {**base, "x": {"x_n60": 0, "x_n15": 0, "x_k": 0, "x_authors": 0, "x_eng": 0}}
    text = fade.ask_text("ABCD", "news", 2.0, 5000, ctx, "")
    assert "𝕏 0 posts last hour (" in text and "newest" not in text


def test_off_unless_enabled(monkeypatch):
    # owner 2026-11-09: X lookups off (no spend) unless SGT_X_ENABLED=1
    _reset(monkeypatch)
    monkeypatch.delenv("SGT_X_ENABLED")
    seen = []
    assert xsocial.lookup("RAYA", AT, client=_client(seen, {"data": [bucket(3, 2)]})) is None and seen == []
