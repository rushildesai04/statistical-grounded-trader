"""Schwab market data reader (owner 2026-11-18, decision B): strict parsing, bounded, fails to None, token never logged."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest

from sgt.intraday import schwab_md as md

T0 = datetime(2026, 11, 18, 15, 0, tzinfo=timezone.utc)
MS = int(T0.timestamp() * 1000)
TOKEN = "tok-SECRET-123"


def candle(i=0, **kw):
    d = {"open": 7.23, "high": 7.63, "low": 7.13, "close": 7.44, "volume": 324988, "datetime": MS + i * 60_000}
    d.update(kw)
    return d


def call(handler, symbol="CLRO", start=T0, end=T0 + timedelta(minutes=5)):
    async def go():
        async with httpx.AsyncClient(base_url="https://x", transport=httpx.MockTransport(handler)) as c:
            return await md.price_history(c, TOKEN, symbol, start, end)
    return asyncio.run(go())


def test_candles_parse_strictly():
    junk = [candle(1, open="7.2"), candle(2, high=True), candle(3, low=-1), candle(4, high=7.0, low=7.1), "x", None,
            candle(5, datetime="soon"), candle(6, volume=float("nan")), {"open": 1}]
    f = md.parse_candles({"candles": [candle(9), candle(0), candle(0, close=7.5)] + junk, "symbol": "CLRO"})
    assert list(f.columns) == md.COLUMNS and len(f) == 2 and f.ts.is_monotonic_increasing
    assert f.ts.iloc[0] == pd.Timestamp(T0) and f.close.iloc[0] == 7.5 and f.low.iloc[0] == 7.13      # the later duplicate wins
    for bad in (None, [], "x", {"candles": "no"}, {"error": "x"}):
        assert md.parse_candles(bad).empty


def test_candles_are_capped():
    f = md.parse_candles({"candles": [candle(i) for i in range(md.MAX_CANDLES + 50)]})
    assert len(f) == md.MAX_CANDLES


def test_request_shape_and_the_token_stays_out_of_the_log(caplog):
    seen = {}

    def handler(req):
        seen.update(path=req.url.path, params=dict(req.url.params), auth=req.headers.get("authorization"))
        return httpx.Response(200, json={"candles": [candle(0), candle(1)]})
    with caplog.at_level(logging.DEBUG):
        f = call(handler)
    assert len(f) == 2 and seen["path"] == md.PATH and seen["auth"] == f"Bearer {TOKEN}"
    p = seen["params"]
    assert (p["symbol"], p["frequencyType"], p["frequency"], p["needExtendedHoursData"]) == ("CLRO", "minute", "1", "true")
    assert int(p["startDate"]) == MS and int(p["endDate"]) == MS + 300_000
    assert TOKEN not in caplog.text


@pytest.mark.parametrize("resp", [lambda: httpx.Response(401, json={"error": "expired"}), lambda: httpx.Response(429),
                                  lambda: httpx.Response(500, text="oops"), lambda: httpx.Response(200, text="<html>")])
def test_a_failed_request_returns_none_and_logs_no_body_or_token(resp, caplog):
    with caplog.at_level(logging.DEBUG):
        assert call(lambda req: resp()) is None
    assert TOKEN not in caplog.text and "expired" not in caplog.text and "oops" not in caplog.text


def test_a_transport_error_returns_none():
    def boom(req):
        raise httpx.ConnectTimeout("slow")
    assert call(boom) is None


@pytest.mark.parametrize("symbol", ["clro", "CL RO", "../accounts", "CLRO?x=1", "", "TOOLONGSYMBOL", "1ABC", None])
def test_a_symbol_that_is_not_plain_is_refused_before_any_request(symbol):
    def never(req):
        raise AssertionError("no request may be sent")
    with pytest.raises(ValueError):
        call(never, symbol=symbol)


def test_naive_or_reversed_times_are_refused():
    with pytest.raises(ValueError):
        call(lambda r: httpx.Response(200, json={}), start=datetime(2026, 11, 18, 15, 0))
    with pytest.raises(ValueError):
        call(lambda r: httpx.Response(200, json={}), start=T0, end=T0)


def frame(rows):
    return pd.DataFrame([(pd.Timestamp(T0) + pd.Timedelta(minutes=i), 1.0, h, lo, 1.0, v) for i, h, lo, v in rows], columns=md.COLUMNS)


def test_agreement_full_tape_needs_matching_extremes_and_volume():
    tape = frame([(i, 7.63, 7.13, 1000) for i in range(20)])
    same = md.agreement(tape.copy(), tape)
    assert same["full_tape"] and same["high_match"] == 1.0 and same["volume_share"] == 1.0 and same["missing_in_schwab"] == 0
    thin = frame([(i, 7.60, 7.16, 5) for i in range(20)])                # the free feed's kind of candle
    a = md.agreement(thin, tape)
    assert not a["full_tape"] and a["low_match"] == 0.0 and a["volume_share"] == pytest.approx(0.005)
    cent = frame([(i, 7.64, 7.12, 950) for i in range(20)])              # a cent off, 95% of the volume: still the tape
    assert md.agreement(cent, tape)["full_tape"]
    holes = md.agreement(tape.iloc[:10], tape)                            # half the minutes missing
    assert not holes["full_tape"] and holes["missing_in_schwab"] == 10
    assert md.agreement(tape, tape.iloc[:0]) == {"tape_minutes": 0, "both": 0, "full_tape": False}
    assert md.agreement(tape.iloc[:0], tape)["full_tape"] is False


def test_freshness_counts_from_the_end_of_the_newest_candle():
    f = frame([(0, 1, 1, 1), (1, 1, 1, 1)])                               # newest candle 15:01, ends 15:02:00
    assert md.freshness(f, T0 + timedelta(minutes=2, seconds=30)) == {"age_s": 30.0, "real_time": True}
    assert md.freshness(f, T0 + timedelta(minutes=17))["real_time"] is False
    assert md.freshness(f.iloc[:0], T0) == {"age_s": None, "real_time": False}


def _script():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import schwab_md_check
    return schwab_md_check


@pytest.mark.parametrize("exc", ["missing", "auth"])
def test_the_check_script_exits_cleanly_without_a_login(exc, monkeypatch, capsys):
    from sgt.core.interfaces import BrokerAuthError
    from sgt.core.secrets import MissingSecretError

    chk = _script()

    def no_login(*a, **k):
        raise MissingSecretError("secret 'schwab_app_key' not found") if exc == "missing" else BrokerAuthError("expired")
    monkeypatch.setattr(chk.SchwabTokenManager, "shared", classmethod(no_login))
    assert chk.main(["CLRO"]) == 2
    out = capsys.readouterr().out
    assert "no usable Schwab login" in out and "schwab_app_key" not in out


def test_the_check_script_refuses_odd_symbols(capsys):
    assert _script().main(["../accounts"]) == 2 and "not plain stock symbols" in capsys.readouterr().out
