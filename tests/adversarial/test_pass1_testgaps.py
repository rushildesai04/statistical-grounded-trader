from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from sgt.fast import xsocial


def _enable(monkeypatch, key="tok"):
    monkeypatch.delenv("SGT_X_ENABLED", raising=False)
    xsocial._CALLS.clear()
    xsocial._CACHE.clear()
    monkeypatch.setattr(xsocial, "get_secret", lambda name, required=True: key)


def test_attention_counts_query_excludes_retweets(monkeypatch):
    """GAP: xsocial.lookup attention (last-hour) counts query drives x_n60, the headline" number.
    If it omits -is:retweet, retweet/bot spam inflates attention. The suite only checked the
    catalyst query (test_catalyst_query_is_targeted) and the attention PATH, never its query text.
    Mutant src/sgt/fast/xsocial.py:140 dropping -is:retweet SURVIVES the suite; this test kills it."""
    at = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)
    end = (at - timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    seen = []

    def handler(req):
        q = req.url.params["query"]
        seen.append((req.url.path, dict(req.url.params)))
        if req.url.path.endswith("/counts/recent"):
            if "OR" in q:
                return httpx.Response(200, json={"data": [{"tweet_count": 0}]})
            return httpx.Response(200, json={"data": [{"start": end, "end": end, "tweet_count": 5}]})
        return httpx.Response(200, json={"data": []})

    _enable(monkeypatch)
    r = xsocial.lookup("RAYA", at, client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert r is not None and r["x_n60"] == 5
    attention_path, attention_params = seen[0]
    assert attention_path.endswith("/counts/recent") and attention_params["granularity"] == "minute"
    assert "-is:retweet" in attention_params["query"]
