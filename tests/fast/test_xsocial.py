from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from sgt.fast import fade, xsocial

AT = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
Z = "%Y-%m-%dT%H:%M:%S.000Z"


def bucket(minutes_ago, n):
    end = AT - timedelta(minutes=minutes_ago)
    return {"start": (end - timedelta(minutes=1)).strftime(Z), "end": end.strftime(Z), "tweet_count": n}


def post(minutes_ago, text, likes=3):
    return {"created_at": (AT - timedelta(minutes=minutes_ago)).strftime(Z), "text": text,
            "public_metrics": {"like_count": likes}}


def _client(seen, attention, catalyst, search=None, status=200):
    def handler(req):
        q = req.url.params["query"]
        seen.append((req.url.path, dict(req.url.params)))
        assert req.url.params["end_time"] == "2026-11-09T14:59:45Z"                       # point in time
        if req.url.path.endswith("/counts/recent"):
            return httpx.Response(200, json=catalyst if "OR" in q else attention)
        return httpx.Response(status, json=search or {"data": []})
    return httpx.Client(transport=httpx.MockTransport(handler))


def _reset(monkeypatch, key="tok"):
    monkeypatch.delenv("SGT_X_ENABLED", raising=False)
    xsocial._CALLS.clear()
    xsocial._CACHE.clear()
    monkeypatch.setattr(xsocial, "get_secret", lambda name, required=True: key)


def test_counts_and_total():
    assert xsocial.counts({"data": [bucket(50, 7), bucket(10, 2), "junk"]}, AT) == {"x_n60": 9, "x_n15": 2}
    assert xsocial.total({"data": [{"tweet_count": 3}, {"tweet_count": "9"}, {"tweet_count": 4}]}) == 7
    assert xsocial.counts({"errors": []}, AT) is None and xsocial.total({}) is None


def test_catalyst_query_is_targeted():
    q = xsocial.catalyst_query("RAYA")
    assert q.startswith("$RAYA (") and "FDA" in q and '"press release"' in q and q.endswith("-is:retweet")


def test_attention_and_catalyst_with_relevant_posts(monkeypatch):
    _reset(monkeypatch)
    seen = []
    c = _client(seen, {"data": [bucket(30, 40), bucket(3, 2)]}, {"data": [{"tweet_count": 4}]},
                {"data": [post(30, "RAYA announces FDA approval\x00", 50), post(-1, "future"), post(90, "contract win &gt; big")]})
    r = xsocial.lookup("RAYA", AT, client=c)
    assert r["x_n60"] == 42 and r["x_cat24"] == 4
    assert [p["text"] for p in r["x_cat_posts"]] == ["RAYA announces FDA approval", "contract win > big"]   # no future post
    paths = [p for p, _ in seen]
    assert paths == ["/2/tweets/counts/recent", "/2/tweets/counts/recent", "/2/tweets/search/recent"]
    assert seen[1][1]["granularity"] == "hour" and seen[2][1]["sort_order"] == "relevancy"
    assert seen[2][1]["max_results"] == "10" and "OR" in seen[2][1]["query"]
    assert xsocial._CALLS == {AT.date().isoformat(): 1}


def test_no_catalyst_skips_the_paid_search(monkeypatch):
    _reset(monkeypatch)
    seen = []
    r = xsocial.lookup("RAYA", AT, client=_client(seen, {"data": [bucket(3, 1)]}, {"data": [{"tweet_count": 0}]}))
    assert r["x_cat24"] == 0 and r["x_cat_posts"] == [] and len(seen) == 2


def test_disabled_dormant_capped_and_unknown(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setenv("SGT_X_ENABLED", "0")
    seen = []
    assert xsocial.lookup("RAYA", AT, client=_client(seen, {}, {})) is None and seen == []
    _reset(monkeypatch, key=None)
    assert xsocial.lookup("RAYA", AT) is None
    _reset(monkeypatch)
    xsocial._CALLS[AT.date().isoformat()] = xsocial.DAILY_CAP
    assert xsocial.lookup("RAYA", AT, client=_client(seen, {}, {})) is None and seen == []
    _reset(monkeypatch)
    assert xsocial.lookup("RAYA", AT, client=_client(seen, {"title": "Payment Required"}, {})) is None
    _reset(monkeypatch)
    r = xsocial.lookup("RAYA", AT, client=_client([], {"data": [bucket(3, 5)]}, {"title": "x"}))
    assert r["x_n60"] == 5 and r["x_cat24"] is None and r["x_cat_posts"] is None              # catalyst unknown


def test_cache_reuses_answer_within_ttl(monkeypatch):
    _reset(monkeypatch)
    seen = []
    c = _client(seen, {"data": [bucket(3, 2)]}, {"data": [{"tweet_count": 0}]})
    a = xsocial.lookup("RAYA", AT, client=c)
    a["x_n60"] = -1
    assert xsocial.lookup("RAYA", AT + timedelta(minutes=9), client=c)["x_n60"] == 2 and len(seen) == 2


def test_entry_text_line():
    base = {"news": {"news_any": 0, "news_own": 0, "news_earn": 0}}
    t = fade.ask_text("ABCD", "volume_spike", 2.0, 5000, {**base, "x": {"x_n60": 42, "x_cat24": 3,
                      "x_cat_posts": [{"text": "ABCD announces contract"}]}}, "")
    assert "𝕏 42 posts last hour; ⚠️ 3 catalyst posts in 24h: “ABCD announces contract”" in t
    t = fade.ask_text("ABCD", "volume_spike", 2.0, 5000, {**base, "x": {"x_n60": 0, "x_cat24": 0, "x_cat_posts": []}}, "")
    assert "𝕏 0 posts last hour; no catalyst posts in 24h" in t
