"""PASS 2 adversarial regression reproducers.

Kills a mutant that survived every pass-1 test: dropping the ``db`` argument from the X lookup inside
``fade_live.iex_context`` (so the live book would use the in-process X cap again, F-5, instead of the
persisted OpsStore counter). Asserts PRODUCTION wiring: the OpsStore reaches xsocial.lookup.

Scope: scripts/fade_live.py iex_context -> src/sgt/fast/xsocial.lookup.
"""
from __future__ import annotations

import httpx

import fade_live as fl


def test_iex_context_threads_the_ops_db_into_the_x_cap(monkeypatch):
    # Mutant `xsocial.lookup(symbol, now)` (no db=) => the live book counts X calls in process memory again
    # (resets on every deploy, F-5). This asserts iex_context hands its OpsStore to the X lookup.
    seen = {}
    monkeypatch.setattr(fl.social, "lookup", lambda symbol, at: None)
    monkeypatch.setattr(fl.reddit, "lookup", lambda symbol, at: None)
    monkeypatch.setattr(fl.filings, "lookup", lambda symbol, at, since_8k=None: None)
    monkeypatch.setattr(fl, "live_news", lambda client, symbol, at: None)

    def fake_x(symbol, at, client=None, db=None):
        seen["db"] = db
        return {"x_n60": 1}

    monkeypatch.setattr(fl.xsocial, "lookup", fake_x)

    sentinel = object()

    def handler(req):
        return httpx.Response(200, json={"latestTrade": {"p": 3.7},
                                         "dailyBar": {"o": 3.3, "h": 4.0, "vw": 3.5, "v": 9e5},
                                         "prevDailyBar": {"c": 2.0, "v": 3e5}})

    ctx = fl.iex_context(httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler)), db=sentinel)
    out = ctx("XYZ")

    assert seen.get("db") is sentinel, (
        "iex_context did not pass its OpsStore to xsocial.lookup; the live X daily cap would fall back to "
        "the in-process counter that resets on every deploy (F-5)."
    )
    assert out["x"] == {"x_n60": 1}
