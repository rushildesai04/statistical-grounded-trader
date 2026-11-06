from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx

from sgt.fast import fade, social

AT = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)


def msg(minutes_ago: float, tag: str | None = None) -> dict:
    t = (AT - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"created_at": t, "entities": {"sentiment": {"basic": tag} if tag else None}}


def test_attention_counts_only_posts_before_the_alert():
    p = {"symbol": {"watchlist_count": 12345},
         "messages": [msg(-5, "Bullish"), msg(2, "Bullish"), msg(10, "Bearish"), msg(40), msg(90, "Bullish"),
                      {"created_at": "garbage"}, "junk"]}
    a = social.attention(p, AT)
    assert a == {"st_n60": 3, "st_n15": 2, "st_span_min": 90.0, "st_bull": 1, "st_bear": 1, "st_watch": 12345}
    assert social.attention({}, AT)["st_span_min"] is None


def test_lookup_bounds_size_and_failures_are_unknown():
    big = json.dumps({"messages": [msg(1)] * 50, "pad": "x" * (social.MAX_BYTES + 10)}).encode()

    def handler(req: httpx.Request) -> httpx.Response:
        if "GOOD" in req.url.path:
            return httpx.Response(200, json={"symbol": {}, "messages": [msg(3, "Bullish")]})
        if "HUGE" in req.url.path:
            return httpx.Response(200, content=big)
        return httpx.Response(429)

    c = httpx.Client(transport=httpx.MockTransport(handler))
    assert social.lookup("GOOD", AT, client=c)["st_n60"] == 1
    assert social.lookup("HUGE", AT, client=c) is None
    assert social.lookup("BUSY", AT, client=c) is None and social.lookup("bad sym", AT, client=c) is None


def test_entry_text_shows_buzz_and_omits_unknown():
    ctx = {"news": {"news_any": 0, "news_own": 0, "news_earn": 0}}
    assert "StockTwits" not in fade.ask_text("ABCD", "volume_spike", 4.0, 5000, ctx, "")
    t = fade.ask_text("ABCD", "volume_spike", 4.0, 5000, {**ctx, "social": {
        "st_n60": 30, "st_n15": 22, "st_span_min": 18.0, "st_bull": 9, "st_bear": 1, "st_watch": 5200}}, "")
    assert "🗣 StockTwits: 30 posts last hour (9 bull / 1 bear); latest 30 span 18 min · 5k watching" in t
