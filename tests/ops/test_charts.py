from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pandas as pd
import pytest

from sgt.ops import charts


def bars(start="2026-11-05T14:30:00Z", n=120, px=4.0):
    ts = pd.date_range(start, periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({"ts": ts, "open": px, "high": px * 1.01, "low": px * 0.99, "close": px, "volume": 1000.0})


TRADE = {"id": 38, "symbol": "DNUT", "session": "2026-11-05", "alert_type": "volume_increasing",
         "posted_at": "2026-11-05T15:11:54+00:00", "status": "est_closed", "reason": "60 min",
         "est_entry": 4.465, "est_entry_at": "2026-11-05T15:12:53+00:00", "est_exit": 4.64,
         "est_exit_at": "2026-11-05T16:13:00+00:00", "conf": 55, "news_own": 1,
         "news_head": "Krispy Kreme Raises FY2026 Sales Guidance"}


def test_axis_prints_eastern_wall_clock():
    t = charts.et_epoch("2026-11-05T15:11:54Z")                            # 10:11:54 ET (EST, UTC-5)
    d = datetime.fromtimestamp(t, timezone.utc)
    assert (d.hour, d.minute) == (10, 11)


def test_estimated_trade_markers_lines_and_result():
    v = charts.trade_view(TRADE, bars(), usd=-196.0)
    assert [m["kind"] for m in v["markers"]] == ["alert", "short", "cover"]
    times = [c["time"] for c in v["candles"]]
    assert all(m["time"] in times for m in v["markers"])                 # markers sit on real bars
    assert v["markers"][0]["time"] == charts.et_epoch("2026-11-05T15:11:00Z")
    assert v["stop"] == pytest.approx(4.465 * 1.2) and v["ret"] == pytest.approx((4.465 - 4.64) / 4.465)
    assert not v["official"] and v["news"] and v["conf"] == 55
    assert all(x["value"] == pytest.approx(4.0) for x in v["vwap"])       # flat bars: VWAP = typical price


def test_settled_trade_shows_official_prices_and_timing():
    t = {**TRADE, "status": "settled", "net": -0.045, "entry": 4.455, "cover": 4.615,
         "entry_ts": "2026-11-05 15:13:00+00:00"}
    v = charts.trade_view(t, bars())
    assert v["official"] and v["entry"] == 4.455 and v["exit"] == 4.615 and v["ret"] == -0.045
    assert v["markers"][1]["time"] == charts.et_epoch("2026-11-05T15:13:00Z")


def test_open_trade_has_no_cover_and_no_result():
    t = {k: v for k, v in TRADE.items() if k not in ("est_exit", "est_exit_at")} | {"status": "open"}
    v = charts.trade_view(t, bars())
    assert [m["kind"] for m in v["markers"]] == ["alert", "short"] and v["ret"] is None


def test_untrusted_text_cannot_break_out_of_the_page():
    evil = {**TRADE, "news_head": "</script><script>alert(1)</script><!--"}
    html = charts.render([charts.trade_view(evil, bars())], datetime(2026, 11, 5, 17, tzinfo=timezone.utc))
    data = re.search(r"const DATA = (.*?);\n", html).group(1)
    assert "<" not in data and "</script><script>" not in html
    assert json.loads(data)["trades"][0]["headline"] == evil["news_head"]
    assert "/*TRADES*/null" not in html and "lightweight-charts@4.2.0" in html
