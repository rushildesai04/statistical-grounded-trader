"""Bar sources: Alpaca pagination/retry/auth/validation, yfinance, live polling.

No network: httpx is mocked with respx; yfinance is replaced by an injected
downloader; the live source is driven by a fake clock + fake async sleep.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest
import respx

from sgt.core.types import Segment
from sgt.intraday.sources import (
    AlpacaHistoricalBars,
    AlpacaPollingLiveSource,
    DataSourceAuthError,
    YFinanceBars,
)

UTC = timezone.utc
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"


@pytest.fixture(autouse=True)
def alpaca_secrets(monkeypatch):
    monkeypatch.setenv("SGT_SECRET_ALPACA_KEY_ID", "test-key")
    monkeypatch.setenv("SGT_SECRET_ALPACA_SECRET_KEY", "test-secret")


def _bar(t: str, o=5.0, h=5.2, l=4.8, c=5.1, v=1000, n=10, vw=5.0) -> dict:
    return {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v, "n": n, "vw": vw}


def _start_end():
    return (datetime(2025, 3, 14, 0, 0, tzinfo=UTC), datetime(2025, 3, 15, 0, 0, tzinfo=UTC))


# --- Alpaca historical -----------------------------------------------------
@respx.mock
def test_alpaca_pagination():
    def handler(request):
        if request.url.params.get("page_token") == "p2":
            return httpx.Response(200, json={"bars": {"WFF": [_bar("2025-03-14T14:32:00Z")]},
                                             "next_page_token": None})
        return httpx.Response(200, json={"bars": {"WFF": [_bar("2025-03-14T14:31:00Z")]},
                                         "next_page_token": "p2"})

    respx.get(BARS_URL).mock(side_effect=handler)
    src = AlpacaHistoricalBars(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    bars = src.fetch("WFF", start, end)
    assert [b.ts for b in bars] == [
        datetime(2025, 3, 14, 14, 31, tzinfo=UTC),
        datetime(2025, 3, 14, 14, 32, tzinfo=UTC),
    ]


@respx.mock
def test_alpaca_429_then_success():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="rate limited")
        return httpx.Response(200, json={"bars": {"WFF": [_bar("2025-03-14T14:31:00Z")]},
                                         "next_page_token": None})

    respx.get(BARS_URL).mock(side_effect=handler)
    src = AlpacaHistoricalBars(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    bars = src.fetch("WFF", start, end)
    assert len(bars) == 1 and calls["n"] == 2


@respx.mock
def test_alpaca_401_raises():
    respx.get(BARS_URL).mock(return_value=httpx.Response(401, text="unauthorized"))
    src = AlpacaHistoricalBars(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    with pytest.raises(DataSourceAuthError):
        src.fetch("WFF", start, end)


@respx.mock
def test_alpaca_malformed_row_skipped(caplog):
    rows = [
        _bar("2025-03-14T14:31:00Z"),
        {"t": "2025-03-14T14:32:00Z", "o": 5.0},  # missing h/l/c/v
        _bar("2025-03-14T14:33:00Z"),
    ]
    respx.get(BARS_URL).mock(
        return_value=httpx.Response(200, json={"bars": {"WFF": rows}, "next_page_token": None})
    )
    src = AlpacaHistoricalBars(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    with caplog.at_level("WARNING"):
        bars = src.fetch("WFF", start, end)
    assert len(bars) == 2
    assert any("skipped 1" in r.message for r in caplog.records)


@respx.mock
def test_alpaca_extended_hours_annotated():
    rows = [
        _bar("2025-03-14T12:00:00Z"),  # 08:00 ET -> PRE
        _bar("2025-03-14T14:31:00Z"),  # 10:31 ET -> RTH
        _bar("2025-03-14T20:30:00Z"),  # 16:30 ET -> POST
        _bar("2025-03-14T07:00:00Z"),  # 03:00 ET -> outside, dropped
    ]
    respx.get(BARS_URL).mock(
        return_value=httpx.Response(200, json={"bars": {"WFF": rows}, "next_page_token": None})
    )
    src = AlpacaHistoricalBars(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    bars = src.fetch("WFF", start, end)
    segs = [b.segment for b in bars]
    assert segs == [Segment.PRE, Segment.RTH, Segment.POST]


@respx.mock
def test_alpaca_fetch_many():
    respx.get(BARS_URL).mock(
        return_value=httpx.Response(200, json={
            "bars": {"WFF": [_bar("2025-03-14T14:31:00Z")],
                     "YMAT": [_bar("2025-03-14T14:31:00Z"), _bar("2025-03-14T14:32:00Z")]},
            "next_page_token": None,
        })
    )
    src = AlpacaHistoricalBars(client=httpx.Client(), sleep=lambda *_: None)
    start, end = _start_end()
    out = src.fetch_many(["WFF", "YMAT"], start, end)
    assert len(out["WFF"]) == 1 and len(out["YMAT"]) == 2


# --- yfinance --------------------------------------------------------------
def test_yfinance_converts_and_drops_bad_rows(caplog):
    et = "America/New_York"
    idx = pd.to_datetime([
        "2025-03-14 10:31:00", "2025-03-14 10:32:00", "2025-03-14 10:33:00",
    ]).tz_localize(et)
    df = pd.DataFrame(
        {
            "Open": [5.0, float("nan"), 5.2],
            "High": [5.3, 5.3, 5.4],
            "Low": [4.9, 4.9, 5.0],
            "Close": [5.1, 5.1, 5.3],
            "Volume": [1000, 0, 2000],
        },
        index=idx,
    )

    def downloader(symbol, start, end, prepost):
        return df

    src = YFinanceBars(downloader=downloader)
    with caplog.at_level("WARNING"):
        bars = src.fetch("WFF", datetime(2025, 3, 14, tzinfo=UTC), datetime(2025, 3, 15, tzinfo=UTC))
    # middle row has NaN Open -> dropped; two valid bars converted to UTC
    assert len(bars) == 2
    assert bars[0].ts == datetime(2025, 3, 14, 14, 31, tzinfo=UTC)
    assert bars[0].segment is Segment.RTH


def test_yfinance_empty_frame():
    src = YFinanceBars(downloader=lambda *a: pd.DataFrame())
    assert src.fetch("WFF", datetime(2025, 3, 14, tzinfo=UTC), datetime(2025, 3, 15, tzinfo=UTC)) == []


# --- live polling source ---------------------------------------------------
class _FakeClock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def test_delayed_feed_sets_delay_and_consolidated():
    src = AlpacaPollingLiveSource(feed="delayed_sip", client=httpx.AsyncClient())
    assert src.delay_minutes == 15 and src.consolidated is True
    assert src.is_live_tradeable is False


@respx.mock
async def test_live_emits_each_completed_bar_once_in_order():
    rows = [_bar(f"2025-03-14T15:4{m}:00Z") for m in range(1, 7)]  # 15:41..15:46
    respx.get(BARS_URL).mock(
        return_value=httpx.Response(200, json={"bars": {"WFF": rows}, "next_page_token": None})
    )
    clock = _FakeClock(datetime(2025, 3, 14, 15, 44, 30, tzinfo=UTC))

    async def fake_sleep(secs):
        clock.now = clock.now + timedelta(seconds=secs)

    src = AlpacaPollingLiveSource(
        feed="sip", client=httpx.AsyncClient(), clock=clock, sleep=fake_sleep
    )
    collected = []
    async for bar in src.stream(["WFF"]):
        collected.append(bar)
        if len(collected) == 5:
            break
    await src.close()
    tss = [b.ts for b in collected]
    assert tss == sorted(tss)
    assert len(set(tss)) == len(tss)  # each emitted exactly once
    # never emits the in-progress minute (bar at the current clock minute)
    assert all(b.ts <= datetime(2025, 3, 14, 15, 45, tzinfo=UTC) for b in collected)


@respx.mock
async def test_live_respects_delay_excludes_recent():
    # delayed_sip: delay 15m; only bars <= now-15m-1m are complete
    rows = [_bar("2025-03-14T15:30:00Z"), _bar("2025-03-14T15:44:00Z"), _bar("2025-03-14T15:50:00Z")]
    respx.get(BARS_URL).mock(
        return_value=httpx.Response(200, json={"bars": {"WFF": rows}, "next_page_token": None})
    )
    clock = _FakeClock(datetime(2025, 3, 14, 16, 0, 10, tzinfo=UTC))

    async def fake_sleep(secs):  # advance a touch, but stop after first batch
        clock.now = clock.now + timedelta(seconds=secs)

    src = AlpacaPollingLiveSource(
        feed="delayed_sip", client=httpx.AsyncClient(), clock=clock, sleep=fake_sleep
    )
    collected = []
    async for bar in src.stream(["WFF"]):
        collected.append(bar)
        if bar.ts == datetime(2025, 3, 14, 15, 44, tzinfo=UTC):
            break
    await src.close()
    # completed_until at start = floor(16:00:10 - 15m) - 1m = 15:44:00 -> 15:30 and 15:44 only
    assert datetime(2025, 3, 14, 15, 30, tzinfo=UTC) in [b.ts for b in collected]
    assert datetime(2025, 3, 14, 15, 50, tzinfo=UTC) not in [b.ts for b in collected]


@respx.mock
async def test_live_auth_error_raises():
    respx.get(BARS_URL).mock(return_value=httpx.Response(403, text="forbidden"))
    src = AlpacaPollingLiveSource(feed="sip", client=httpx.AsyncClient())
    with pytest.raises(DataSourceAuthError):
        async for _ in src.stream(["WFF"]):
            pass
    await src.close()
