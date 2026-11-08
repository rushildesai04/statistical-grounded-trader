from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from sgt.fast import fade, xsocial

AT = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


def post(minutes_ago, author, likes):
    return {"created_at": (AT - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S.000Z"), "author_id": author,
            "public_metrics": {"like_count": likes, "retweet_count": 1, "reply_count": 0, "quote_count": 0}}


def test_attention_is_point_in_time():
    a = xsocial.attention({"data": [post(5, "a", 10), post(30, "b", 2), post(-2, "c", 99), "junk"]}, AT)
    assert a == {"x_n60": 2, "x_n15": 1, "x_authors": 2, "x_eng": 14}


def test_dormant_without_key_and_capped(monkeypatch):
    xsocial._CALLS.clear()
    monkeypatch.setattr(xsocial, "get_secret", lambda name, required=True: None)
    assert xsocial.lookup("RAYA", AT) is None and xsocial._CALLS == {}          # no key: no call, nothing counted
    monkeypatch.setattr(xsocial, "get_secret", lambda name, required=True: "tok")
    seen = []

    def handler(req):
        seen.append(req.url.params["query"])
        assert req.url.params["end_time"] == "2026-11-09T14:59:45Z"
        return httpx.Response(200, json={"data": [post(3, "a", 5)]})

    c = httpx.Client(transport=httpx.MockTransport(handler))
    assert xsocial.lookup("RAYA", AT, client=c)["x_n60"] == 1 and seen == ["$RAYA -is:retweet"]
    xsocial._CALLS[AT.date().isoformat()] = xsocial.DAILY_CAP
    assert xsocial.lookup("RAYA", AT, client=c) is None and len(seen) == 1      # cap reached: no call
    xsocial._CALLS.clear()
    assert xsocial.lookup("bad sym", AT, client=c) is None


def test_entry_text_line():
    ctx = {"news": {"news_any": 0, "news_own": 0, "news_earn": 0},
           "x": {"x_n60": 42, "x_n15": 9, "x_authors": 30, "x_eng": 1234}}
    assert "𝕏 42 posts last hour from 30 accounts, 1,234 likes/reposts" in fade.ask_text("ABCD", "news", 2.0, 5000, ctx, "")
