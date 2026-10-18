"""TradingView webhook receiver + golden-check tests (no network; TestClient only)."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from sgt.api.webhook_app import WEBHOOK_SECRET_NAME, create_app
from sgt.core.types import Bar
from sgt.crowd.tradingview import (
    INDICATOR_MAP,
    load_indicators,
    point_in_time_snapshot,
    recent_tradingview_symbols,
    store_alert,
)
from sgt.crowd.tradingview_models import TradingViewAlert
from sgt.db.session import get_session
from sgt.intraday.calendar import default_calendar
from sgt.intraday.store import ParquetBarStore
from sgt.util import to_utc

_SECRET = "s3cr3t-shared-value"
_SECRET_ENV = "SGT_SECRET_TRADINGVIEW_WEBHOOK_SECRET"


def _load_golden_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "tradingview_golden_check.py"
    spec = importlib.util.spec_from_file_location("tradingview_golden_check", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["tradingview_golden_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def _valid_body(**over) -> dict:
    body = {
        "secret": _SECRET,
        "ticker": "AAPL",
        "exchange": "NASDAQ",
        "time": "2026-10-09T13:45:00Z",
        "interval": "1",
        "close": 190.25,
        "volume": 12345.0,
        "alert_name": "ema_cross",
        "indicators": {"ema9": 190.1, "rsi14": 61.2},
    }
    body.update(over)
    return body


# --------------------------------------------------------------------------- auth / fail-closed


def test_503_when_secret_unconfigured(monkeypatch):
    monkeypatch.delenv(_SECRET_ENV, raising=False)
    client = TestClient(create_app())
    r = client.post("/tradingview/alert", json=_valid_body())
    assert r.status_code == 503


def test_401_on_wrong_secret(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    client = TestClient(create_app())
    r = client.post("/tradingview/alert", json=_valid_body(secret="wrong"))
    assert r.status_code == 401
    # Nothing stored on a failed auth.
    with get_session() as s:
        assert s.execute(select(TradingViewAlert)).first() is None


def test_200_and_stored(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    client = TestClient(create_app())
    r = client.post("/tradingview/alert", json=_valid_body())
    assert r.status_code == 200
    assert r.json()["status"] == "stored"
    with get_session() as s:
        row = s.execute(select(TradingViewAlert)).scalar_one()
        assert row.ticker == "AAPL"
        # SQLite returns tz-naive datetimes; normalize for the comparison.
        assert to_utc(row.ts) == datetime(2026, 10, 9, 13, 45, tzinfo=timezone.utc)
        assert load_indicators(row.indicators_json) == {"ema9": 190.1, "rsi14": 61.2}


# --------------------------------------------------------------------------- input validation


def test_413_oversize(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    client = TestClient(create_app())
    raw = b'{"secret":"' + b"a" * 9000 + b'"}'
    r = client.post(
        "/tradingview/alert", content=raw, headers={"content-type": "application/json"}
    )
    assert r.status_code == 413


def test_422_invalid_body(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    client = TestClient(create_app())
    r = client.post("/tradingview/alert", json={"secret": _SECRET})  # missing required fields
    assert r.status_code == 422


def test_422_extra_field_rejected(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    client = TestClient(create_app())
    r = client.post("/tradingview/alert", json=_valid_body(surprise="nope"))
    assert r.status_code == 422


def test_422_too_many_indicators(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    client = TestClient(create_app())
    inds = {f"k{i}": float(i) for i in range(33)}
    r = client.post("/tradingview/alert", json=_valid_body(indicators=inds))
    assert r.status_code == 422


# --------------------------------------------------------------------------- idempotency / rate limit


def test_idempotent_duplicate(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    client = TestClient(create_app())
    first = client.post("/tradingview/alert", json=_valid_body())
    second = client.post("/tradingview/alert", json=_valid_body())
    assert first.json()["status"] == "stored"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    with get_session() as s:
        assert len(s.execute(select(TradingViewAlert)).scalars().all()) == 1


def test_rate_limit_429(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    client = TestClient(create_app(rate_capacity=1, rate_refill_per_s=0.001))
    first = client.post("/tradingview/alert", json=_valid_body())
    second = client.post("/tradingview/alert", json=_valid_body(alert_name="other"))
    assert first.status_code == 200
    assert second.status_code == 429


def test_secret_never_persisted(monkeypatch):
    monkeypatch.setenv(_SECRET_ENV, _SECRET)
    client = TestClient(create_app())
    client.post("/tradingview/alert", json=_valid_body())
    with get_session() as s:
        row = s.execute(select(TradingViewAlert)).scalar_one()
    for col in TradingViewAlert.__table__.columns:
        val = getattr(row, col.name)
        assert not (isinstance(val, str) and _SECRET in val)
    assert not hasattr(row, "secret")


# --------------------------------------------------------------------------- candidate source


def test_recent_symbols_query():
    now = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)
    with get_session() as s:
        store_alert(s, alert_name="a", ticker="aapl", ts=now, exchange=None, interval=None,
                    close=1.0, volume=1.0, indicators=None, received_at=now)
        store_alert(s, alert_name="a", ticker="MSFT", ts=now - timedelta(days=2),
                    exchange=None, interval=None, close=1.0, volume=1.0,
                    indicators=None, received_at=now)
    with get_session() as s:
        recent = recent_tradingview_symbols(s, now - timedelta(hours=1))
        assert recent == ["AAPL"]
        wide = recent_tradingview_symbols(s, now - timedelta(days=5))
        assert wide == ["AAPL", "MSFT"]


# --------------------------------------------------------------------------- golden check


def _session_bars(symbol: str, n: int) -> list[Bar]:
    cal = default_calendar()
    # 2026-10-09 is a trading day; 09:30 ET == 13:30 UTC (EDT, UTC-4).
    start = datetime(2026, 10, 9, 13, 30, tzinfo=timezone.utc)
    bars: list[Bar] = []
    price = 100.0
    for i in range(n):
        ts = start + timedelta(minutes=i)
        # Deterministic gentle oscillation so RSI/MACD are non-degenerate.
        price += math.sin(i / 3.0) * 0.5 + 0.05
        o = price
        c = price + math.cos(i / 4.0) * 0.3
        hi = max(o, c) + 0.2
        lo = min(o, c) - 0.2
        bar = cal.annotate(symbol, ts, o, hi, lo, c, 1000.0 + i * 10.0)
        assert bar is not None
        bars.append(bar)
    return bars


def test_golden_check_pass_and_fail(tmp_path, monkeypatch):
    mod = _load_golden_module()
    symbol = "TEST"
    bars = _session_bars(symbol, 50)
    store = ParquetBarStore(tmp_path)
    store.write_session(symbol, bars[0].session_date, bars)

    target = bars[-1]
    names = ["ema9", "rsi14", "macd_hist", "vwap"]
    specs = list({INDICATOR_MAP[n][0] for n in names})
    snap = point_in_time_snapshot(bars, specs)
    tv_values = {}
    for n in names:
        spec, out = INDICATOR_MAP[n]
        v = snap[f"{spec.key}.{out}"]
        assert v is not None  # indicators must be ready on 50 bars
        tv_values[n] = v

    now = datetime.now(timezone.utc)
    with get_session() as s:
        store_alert(s, alert_name="golden", ticker=symbol, ts=target.ts, exchange=None,
                    interval="1", close=target.close, volume=target.volume,
                    indicators=tv_values, received_at=now)

    since = datetime(2026, 10, 9, tzinfo=timezone.utc)
    with get_session() as s:
        rows = mod.run_golden_check(s, store, since=since, lookback_sessions=0,
                                    abs_tol=1e-6, rel_tol=1e-6)
    assert rows and all(r.status == "pass" for r in rows), [(r.name, r.status) for r in rows]

    # Perturb one stored value -> that comparison must fail.
    with get_session() as s:
        row = s.execute(select(TradingViewAlert)).scalar_one()
        perturbed = dict(tv_values)
        perturbed["rsi14"] = perturbed["rsi14"] + 5.0
        row.indicators_json = json.dumps(perturbed, separators=(",", ":"))
    with get_session() as s:
        rows = mod.run_golden_check(s, store, since=since, lookback_sessions=0,
                                    abs_tol=1e-6, rel_tol=1e-6)
    by_name = {r.name: r for r in rows}
    assert by_name["rsi14"].status == "fail"
    assert by_name["ema9"].status == "pass"


def test_golden_check_no_bar(tmp_path):
    mod = _load_golden_module()
    store = ParquetBarStore(tmp_path)  # empty store
    ts = datetime(2026, 10, 9, 13, 45, tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    with get_session() as s:
        store_alert(s, alert_name="g", ticker="NONE", ts=ts, exchange=None, interval=None,
                    close=1.0, volume=1.0, indicators={"ema9": 1.0}, received_at=now)
    with get_session() as s:
        rows = mod.run_golden_check(s, store, since=datetime(2026, 10, 1, tzinfo=timezone.utc),
                                    lookback_sessions=0, abs_tol=1e-6, rel_tol=1e-6)
    assert rows and all(r.status == "no_bar" for r in rows)
