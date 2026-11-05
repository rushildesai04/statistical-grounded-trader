from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sgt.crypto import coinbase as cb

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def row(minute: int, px: float = 100.0) -> list:
    return [int((T0 + timedelta(minutes=minute)).timestamp()), px - 1, px + 1, px, px + 0.5, 3.0]


def test_parse_keeps_valid_rows_and_drops_hostile_ones():
    body = json.dumps([row(1), row(0), row(0), ["x", 1, 2, 3, 4, 5], [1, 2, 3], row(400),
                       [int(T0.timestamp()) + 30, 1, 2, 1.5, 1.5, 1], [int(T0.timestamp()) + 120, 5, 4, 4.5, 4.5, 1],
                       [int(T0.timestamp()) + 180, 1, 2, 1.5, 1.5, -1]]).encode()
    df = cb.parse(body, T0, T0 + cb.STEP)
    assert list(df.ts) == [T0, T0 + timedelta(minutes=1)] and df.open.iloc[0] == 100.0
    with pytest.raises(ValueError):
        cb.parse(json.dumps([row(0)] * 301).encode(), T0, T0 + cb.STEP)
    with pytest.raises(ValueError):
        cb.parse(b"[" + b" " * cb.MAX_BYTES + b"]", T0, T0 + cb.STEP)
    with pytest.raises(ValueError):
        cb.parse(json.dumps({"message": "nope"}).encode(), T0, T0 + cb.STEP)


def test_fetch_pages_in_300_minute_calls_and_retries_429_then_gives_up():
    calls = []

    def handler(req):
        calls.append(dict(req.url.params))
        if len(calls) == 1:
            return httpx.Response(429)
        start = datetime.fromisoformat(req.url.params["start"])
        return httpx.Response(200, json=[row(int((start - T0).total_seconds() // 60))])
    c = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    df = cb.fetch(c, "BTC-USD", T0, T0 + timedelta(minutes=600), sleep=lambda s: None)
    assert len(calls) == 3 and list(df.ts) == [T0, T0 + timedelta(minutes=300)]
    assert calls[-1]["end"] == (T0 + timedelta(minutes=599)).isoformat() and calls[-1]["granularity"] == "60"
    dead = httpx.Client(base_url="https://x", transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(RuntimeError, match="failed after"):
        cb.fetch(dead, "BTC-USD", T0, T0 + timedelta(minutes=10), sleep=lambda s: None)


def test_fetch_month_writes_once_and_load_reads_back(tmp_path):
    n = []

    def handler(req):
        n.append(1)
        return httpx.Response(200, json=[])
    c = httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler))
    assert cb.fetch_month(c, tmp_path, "ETH-USD", "2024-02", sleep=lambda s: None) is not None
    calls = len(n)
    assert calls == -(-29 * 24 * 60 // 300)                     # Feb 2024: 29 days in 300-minute calls
    assert cb.fetch_month(c, tmp_path, "ETH-USD", "2024-02", sleep=lambda s: None) is None and len(n) == calls
    assert cb.load(tmp_path, "ETH-USD", ["2024-02", "2024-03"]).empty
