from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sgt.fast import fade, reddit

AT = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


def row(rank, ticker, mentions, prev=None):
    return {"rank": rank, "ticker": ticker, "name": "x", "mentions": mentions, "upvotes": -2, "rank_24h_ago": 0,
            "mentions_24h_ago": prev}


PAGES = {1: {"count": 3, "pages": 2, "current_page": 1, "results": [row(1, "NVDA", 502, 400), row(2, "raya", "85")]},
         2: {"count": 3, "pages": 2, "current_page": 2,
             "results": [row(3, "GME", 40), row(4, "BAD SYM", 9), "junk", row(5, "NEG", -1), row(2, "RAYA", 1)]}}


@pytest.fixture(autouse=True)
def fresh():
    reddit._TABLE.update(at=None, rows=None)
    yield
    reddit._TABLE.update(at=None, rows=None)


def client(seen, pages=PAGES, fail=None):
    def handler(req):
        p = int(req.url.path.rsplit("/", 1)[-1])
        seen.append(p)
        if p == fail:
            return httpx.Response(503)
        return httpx.Response(200, json=pages[p])
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parse_is_strict():
    rows, pages = reddit.parse(PAGES[2])
    assert set(rows) == {"GME", "RAYA"} and pages == 2                          # bad ticker, junk, negative dropped
    assert reddit.parse({"error": "x"}) is None and reddit.parse([]) is None
    assert reddit.parse({"results": [], "pages": 999})[1] == reddit.MAX_PAGES   # page count bounded


def test_lookup_listed_unlisted_and_cache():
    seen = []
    c = client(seen)
    assert reddit.lookup("NVDA", AT, c) == {"rd_rank": 1, "rd_mentions": 502, "rd_upvotes": -2, "rd_mentions_prev": 400}
    assert reddit.lookup("RAYA", AT, c)["rd_rank"] == 2                         # first (better) rank kept, str parsed
    assert reddit.lookup("ZZZZ", AT, c) == {"rd_rank": None, "rd_mentions": 0, "rd_upvotes": None,
                                            "rd_mentions_prev": None}           # not listed = not discussed
    assert seen == [1, 2]                                                        # one table fetch for all three
    reddit.lookup("GME", AT + timedelta(minutes=11), c)
    assert seen == [1, 2, 1, 2]                                                  # stale: refetched
    assert reddit.lookup("bad sym", AT, c) is None


def test_any_failed_page_is_unknown_not_unlisted():
    assert reddit.lookup("GME", AT, client([], fail=2)) is None
    assert reddit.lookup("GME", AT, client([], fail=1)) is None
    assert reddit._TABLE["rows"] is None                                         # a failure is never cached


def test_entry_text_lines():
    base = {"news": {"news_any": 0, "news_own": 0, "news_earn": 0}}
    t = fade.ask_text("RAYA", "news", 2.0, 5000, {**base, "reddit": {"rd_rank": 2, "rd_mentions": 85,
                                                                     "rd_upvotes": 3, "rd_mentions_prev": 20}}, "")
    assert "👽 Reddit: #2 most-mentioned, 85 mentions in 24h (was 20 a day earlier)" in t
    t = fade.ask_text("ZZZZ", "news", 2.0, 5000, {**base, "reddit": {"rd_rank": None, "rd_mentions": 0}}, "")
    assert "👽 Reddit: not among the ~700 most-mentioned stock tickers today" in t
    assert "Reddit" not in fade.ask_text("ZZZZ", "news", 2.0, 5000, base, "")   # unknown: no line


def test_table_gives_up_when_the_site_is_slow(monkeypatch):
    clock = iter([0.0, 100.0, 200.0])
    monkeypatch.setattr(reddit.time, "monotonic", lambda: next(clock))
    assert reddit.lookup("GME", AT, client([])) is None and reddit._TABLE["rows"] is None
