"""2026-11-12 (owner: the trade analyses were wrong): the AI review gets ET times and an all-venue (SIP) minute path."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pandas as pd
import pytest

import fade_live
from sgt.ai import trade_review as tr
from sgt.ops.store import OpsStore


def test_times_are_given_in_et_with_the_right_offset_in_winter_and_summer():
    assert tr.et_time("2026-11-12T16:53:00+00:00") == "2026-11-12 11:53:00 ET"        # EST, UTC-5 (XLAB)
    assert tr.et_time("2026-07-01T14:00:00+00:00") == "2026-07-01 10:00:00 ET"        # EDT, UTC-4
    assert tr.et_time("2026-11-12T16:44:13.224676+00:00") == "2026-11-12 11:44:13 ET"
    assert tr.et_time("27.04") is None and tr.et_time("2026-11-12") is None and tr.et_time(None) is None


def test_facts_convert_every_trade_time_and_name_the_feed():
    e = {"key": "long:23", "book": "long book", "symbol": "XLAB", "entry_ts": "2026-11-12T16:53:00+00:00",
         "est_exit_at": "2026-11-12T17:06:00+00:00", "posted_at": "2026-11-12T16:43:10+00:00", "est_entry": 1.36}
    b = pd.DataFrame({"ts": pd.to_datetime(["2026-11-12T16:53:00Z"]), "open": [1.3], "high": [1.4], "low": [1.3],
                      "close": [1.35], "volume": [100]})
    f = tr.facts(e, b, None, tested="")
    assert f["trade"]["entry_ts"] == "2026-11-12 11:53:00 ET" and f["trade"]["posted_at"] == "2026-11-12 11:43:10 ET"
    assert f["trade"]["est_entry"] == 1.36 and f["minute_path_ET_o_h_l_c_v"][0][0] == "11:53"
    assert f["minute_path_feed"].startswith("IEX") and "one exchange only" in f["minute_path_feed"]
    b.attrs["feed"] = "SIP"
    assert tr.facts(e, b, None, tested="")["minute_path_feed"] == "SIP 1-minute bars"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return OpsStore(tmp_path / "ops.db")


def test_a_first_review_waits_until_sip_covers_the_window(db):
    exit_at = datetime(2026, 11, 12, 17, 29, tzinfo=timezone.utc)
    with db.tx() as c:
        c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                  "est_entry, est_exit, est_exit_at, created_at) VALUES ('XLAB','2026-11-12','gaining_momentum',"
                  "'2026-11-12T16:43:10+00:00','c',1,'est_closed',1.29,1.56,?,'x')", (exit_at.isoformat(),))
    assert tr.pending(db, now=exit_at + timedelta(minutes=10)) == []
    assert [e["key"] for e in tr.pending(db, now=exit_at + tr.REVIEW_DELAY)] == ["fade:1"]


def _client(answers, seen):
    def handler(req):
        seen.append(dict(req.url.params))
        rows = answers.get(req.url.params["feed"], [])
        return httpx.Response(200, json={"bars": rows})
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="https://data.example")


ROW = {"t": "2026-11-12T14:47:00Z", "o": 8.33, "h": 9.12, "l": 8.0, "c": 9.12, "v": 165768}


def test_ai_bars_prefers_sip_capped_15_minutes_back_and_falls_back_to_iex():
    now = datetime(2026, 11, 12, 15, 30, tzinfo=timezone.utc)
    start, end = now - timedelta(minutes=60), now
    seen = []
    f = fade_live.ai_bars(_client({"sip": [ROW], "iex": [ROW]}, seen), "RETO", start, end, now=lambda: now)
    assert f.attrs["feed"] == "SIP" and len(seen) == 1 and seen[0]["feed"] == "sip"
    assert pd.Timestamp(seen[0]["end"]) <= now - fade_live.SIP_LAG                     # never past the free window
    seen.clear()
    g = fade_live.ai_bars(_client({"iex": [ROW]}, seen), "RETO", start, end, now=lambda: now)
    assert g.attrs["feed"] == "IEX" and [s["feed"] for s in seen] == ["sip", "iex"]
    assert fade_live.ai_bars(_client({}, []), "RETO", start, end, now=lambda: now).empty
    assert fade_live.ai_bars(_client({"sip": [ROW]}, []), "bad sym!", start, end, now=lambda: now).empty
