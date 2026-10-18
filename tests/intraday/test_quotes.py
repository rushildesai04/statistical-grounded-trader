"""Alpaca quote client + spread sampler: validation, pagination bound, time-weighted
spread, recency skipping, and persistence. No network (respx); no real keychain."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx

from sgt.db.session import get_session
from sgt.intraday.calendar import default_calendar
from sgt.intraday.quotes import (
    AlpacaQuotes,
    Quote,
    QuoteFetch,
    SpreadSampleRow,
    SpreadSampler,
    SpreadSample,
    persist_samples,
    tod_bucket,
    window_spread,
)
from sgt.intraday.sources import DataSourceError

UTC = timezone.utc
QUOTES_URL = "https://data.alpaca.markets/v2/stocks/quotes"
from datetime import date


@pytest.fixture(autouse=True)
def alpaca_secrets(monkeypatch):
    monkeypatch.setenv("SGT_SECRET_ALPACA_KEY_ID", "test-key")
    monkeypatch.setenv("SGT_SECRET_ALPACA_SECRET_KEY", "test-secret")


def _q(t: str, bp=2.68, ap=2.69, bs=100, as_=100) -> dict:
    return {"t": t, "bp": bp, "ap": ap, "bs": bs, "as": as_, "bx": "V", "ax": "V", "c": ["R"], "z": "C"}


def _start_end():
    return (datetime(2024, 3, 12, 14, 0, tzinfo=UTC), datetime(2024, 3, 12, 14, 1, tzinfo=UTC))


# --- validation ------------------------------------------------------------
@respx.mock
def test_crossed_and_locked_excluded_and_counted():
    rows = [
        _q("2024-03-12T14:00:01Z", bp=2.68, ap=2.69),   # valid
        _q("2024-03-12T14:00:02Z", bp=2.70, ap=2.69),   # crossed (ask < bid)
        _q("2024-03-12T14:00:03Z", bp=2.69, ap=2.69),   # locked (ask == bid)
        _q("2024-03-12T14:00:04Z", bp=0.0, ap=2.69),    # non-positive bid -> malformed
        {"t": "2024-03-12T14:00:05Z", "bp": 2.68},       # missing ap -> malformed
    ]
    respx.get(QUOTES_URL).mock(return_value=httpx.Response(
        200, json={"quotes": {"WFF": rows}, "next_page_token": None}))
    src = AlpacaQuotes(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    res = src.fetch_quotes("WFF", start, end)
    assert len(res.quotes) == 1
    assert res.crossed == 1 and res.locked == 1 and res.malformed == 2
    assert abs(res.quotes[0].rel_spread - (0.01 / 2.685)) < 1e-9


@respx.mock
def test_401_raises():
    from sgt.intraday.sources import DataSourceAuthError
    respx.get(QUOTES_URL).mock(return_value=httpx.Response(403, text="forbidden"))
    src = AlpacaQuotes(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    with pytest.raises(DataSourceAuthError):
        src.fetch_quotes("WFF", start, end)


@respx.mock
def test_pagination_bound():
    def handler(request):
        # always hand back another page token -> must trip the page cap, not loop forever
        return httpx.Response(200, json={"quotes": {"WFF": [_q("2024-03-12T14:00:01Z")]},
                                         "next_page_token": "more"})
    respx.get(QUOTES_URL).mock(side_effect=handler)
    src = AlpacaQuotes(client=httpx.Client(), sleep=lambda *_: None, max_pages=3)
    start, end = _start_end()
    with pytest.raises(DataSourceError):
        src.fetch_quotes("WFF", start, end)


@respx.mock
def test_pagination_follows_tokens():
    def handler(request):
        if request.url.params.get("page_token") == "p2":
            return httpx.Response(200, json={"quotes": {"WFF": [_q("2024-03-12T14:00:02Z")]},
                                             "next_page_token": None})
        return httpx.Response(200, json={"quotes": {"WFF": [_q("2024-03-12T14:00:01Z")]},
                                         "next_page_token": "p2"})
    respx.get(QUOTES_URL).mock(side_effect=handler)
    src = AlpacaQuotes(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    res = src.fetch_quotes("WFF", start, end)
    assert [q.ts for q in res.quotes] == [
        datetime(2024, 3, 12, 14, 0, 1, tzinfo=UTC),
        datetime(2024, 3, 12, 14, 0, 2, tzinfo=UTC),
    ]


@respx.mock
def test_transport_error_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("server disconnected", request=request)
        return httpx.Response(200, json={"quotes": {"WFF": [_q("2024-03-12T14:00:01Z")]},
                                         "next_page_token": None})

    respx.get(QUOTES_URL).mock(side_effect=handler)
    src = AlpacaQuotes(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    res = src.fetch_quotes("WFF", start, end)
    assert len(res.quotes) == 1 and calls["n"] == 2


@respx.mock
def test_transport_error_exhausted_raises_datasource_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    respx.get(QUOTES_URL).mock(side_effect=handler)
    src = AlpacaQuotes(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    with pytest.raises(DataSourceError):
        src.fetch_quotes("WFF", start, end)


# --- time-weighted spread --------------------------------------------------
def test_window_spread_time_weighted():
    t0 = datetime(2024, 3, 12, 14, 0, 0, tzinfo=UTC)
    quotes = [
        Quote(t0, 10.0, 10.1, 100, 100),                       # rel ~0.00995, dwell 30s
        Quote(t0 + timedelta(seconds=30), 10.0, 10.2, 100, 100),  # rel ~0.0197, dwell 30s
    ]
    rel, mid = window_spread(quotes, t0 + timedelta(seconds=60))
    assert abs(rel - quotes[0].rel_spread) < 1e-9  # median lands on the first (tie -> lower)
    assert abs(mid - 10.075) < 1e-6


def test_window_spread_empty():
    assert window_spread([], datetime(2024, 3, 12, tzinfo=UTC)) is None


def test_tod_bucket_boundaries():
    assert tod_bucket(600) == "opening"      # 10:00 ET
    assert tod_bucket(405) == "pre"
    assert tod_bucket(930) == "power_hour"
    assert tod_bucket(1000) == "post"


# --- sampler ---------------------------------------------------------------
def _fake_fetch(sym, start, end):
    return QuoteFetch(
        quotes=[Quote(start, 2.68, 2.69, 100, 100),
                Quote(start + timedelta(seconds=30), 2.675, 2.695, 100, 100)],
        crossed=0, locked=0, malformed=0)


def test_sampler_samples_all_windows_when_old():
    d = date(2024, 3, 12)
    far_future = datetime(2026, 1, 1, tzinfo=UTC)
    sampler = SpreadSampler(_fake_fetch, sleep=lambda *_: None, now=lambda: far_future)
    samples = sampler.sample_session("WFF", d, windows=8)
    assert len(samples) == 8
    assert all(s.n_quotes == 2 and s.rel_spread_median > 0 for s in samples)


def test_sampler_skips_windows_inside_recency_wall():
    d = date(2024, 3, 12)
    rth_close = default_calendar().bounds(d).rth_close
    sampler = SpreadSampler(_fake_fetch, sleep=lambda *_: None, now=lambda: rth_close)
    samples = sampler.sample_session("WFF", d, windows=8)
    # the post window (rth_close + 30m) is newer than now-15m and must be skipped
    assert samples
    assert all(s.segment != "post" for s in samples)


def test_sampler_rejects_bad_windows_arg():
    sampler = SpreadSampler(_fake_fetch, sleep=lambda *_: None, now=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    with pytest.raises(ValueError):
        sampler.sample_session("WFF", date(2024, 3, 12), windows=0)


# --- persistence -----------------------------------------------------------
def test_persist_and_reload_idempotent():
    s = SpreadSample("WFF", date(2024, 3, 12), 600, "rth", 0.004, 50, 2.685)
    with get_session() as session:
        assert persist_samples(session, [s]) == 1
    # re-persist the same key with a new value -> upsert, not duplicate
    s2 = SpreadSample("WFF", date(2024, 3, 12), 600, "rth", 0.006, 70, 2.685)
    with get_session() as session:
        persist_samples(session, [s2])
    with get_session() as session:
        rows = session.query(SpreadSampleRow).all()
        assert len(rows) == 1
        assert abs(rows[0].rel_spread_median - 0.006) < 1e-12
        assert rows[0].source == "alpaca_sip"
