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


def test_runner_page_is_a_full_document_and_the_artifact_copy_is_a_fragment():
    v = [charts.trade_view(TRADE, bars())]
    now = datetime(2026, 11, 5, 17, tzinfo=timezone.utc)
    full, frag = charts.render(v, now), charts.render(v, now, standalone=False)
    assert full.startswith("<!doctype html>") and '<meta charset="utf-8">' in full and "viewport-fit=cover" in full
    assert frag.startswith("<title>SGT Fade Tape</title>") and full.endswith(frag)
    assert "Rushil's Agent" in frag


def test_ema_9_21_lines_ride_along_for_display():
    v = charts.trade_view(TRADE, bars())
    assert len(v["ema9"]) == len(v["ema21"]) == len(v["candles"])
    assert all(x["value"] == pytest.approx(4.0) for x in v["ema9"] + v["ema21"])    # flat bars: flat EMAs


def test_tape_oscillators_align_to_candles_and_signals_at_entry():
    v = charts.trade_view(TRADE, bars())
    # one point per candle (whitespace carries None) so the synced panes keep bar indices aligned with the chart
    assert len(v["rsi"]) == len(v["macd"]) == len(v["candles"])
    assert {"time", "value"} == set(v["rsi"][0]) and {"time", "line", "signal", "hist"} == set(v["macd"][0])
    assert all(x["value"] is None for x in v["rsi"])                 # flat closes: Wilder RSI undefined all day
    sig = v["signals"]
    assert sig["vwap_dist_pct"] == pytest.approx(0.0) and sig["ema9_21_gap_pct"] == pytest.approx(0.0)
    assert sig["psar_side"] in (-1.0, 1.0) and sig["minutes_since_open"] is not None


def test_signals_values_are_json_safe_and_cannot_break_out():
    evil = {**TRADE, "symbol": "</script><x>"}
    html = charts.render([charts.trade_view(evil, bars())], datetime(2026, 11, 5, 17, tzinfo=timezone.utc))
    data = re.search(r"const DATA = (.*?);\n", html).group(1)
    assert "<" not in data                                           # untrusted symbol + numeric signals stay inside
    assert json.loads(data)["trades"][0]["symbol"] == evil["symbol"]


def test_learning_scoreboard_rows_are_sanitized_for_the_page():
    rows = [{"challenger": "</script><b>X</b>", "prereg": "p", "n": 34, "champ": -0.0148, "chal": float("nan"),
             "diff": 0.07, "lower": None, "status": "collecting 34/300", "extra": "dropped"}]
    clean = charts.learning_rows(rows)
    assert clean == [{"challenger": "</script><b>X</b>", "prereg": "p", "n": 34.0, "champ": -0.0148, "chal": None,
                      "diff": 0.07, "lower": None, "status": "collecting 34/300"}]
    html = charts.render([charts.trade_view(TRADE, bars())], datetime(2026, 11, 6, 17, tzinfo=timezone.utc),
                         learning=rows)
    assert "</script><b>" not in html and '"learning": [{"challenger": "\\u003c/script>' in html
    assert charts.learning_rows(None) == []


LONG = {"id": 7, "symbol": "KULR", "session": "2026-11-05", "alert_type": "volume_spike",
        "posted_at": "2026-11-05T15:11:54+00:00", "status": "est_closed", "reason": "target",
        "est_entry": 4.0, "est_entry_at": "2026-11-05T15:13:00+00:00", "est_exit": 4.2,
        "est_exit_at": "2026-11-05T15:20:00+00:00", "stop": 3.9, "target": 4.2, "setup": "red-candle break"}


def test_long_trade_has_buy_sell_markers_own_stop_target_and_long_return():
    """Owner 2026-11-10 option A: longs on the chart page, with the setup's own stop and target."""
    v = charts.trade_view(LONG, bars(), usd=250.0, side="long")
    assert [m["kind"] for m in v["markers"]] == ["alert", "buy", "sell"]
    assert v["markers"][1]["text"] == "buy 4.00" and v["markers"][2]["text"] == "sell 4.20"
    assert (v["side"], v["stop"], v["target"], v["setup"]) == ("long", 3.9, 4.2, "red-candle break")
    assert v["ret"] == pytest.approx(0.05)                               # long: exit / entry - 1
    short = charts.trade_view(TRADE, bars())
    assert short["side"] == "short" and short["target"] is None


def test_long_trades_rows_use_the_official_result_once_settled(tmp_path, monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import trade_charts as tc
    from sgt.ops.store import OpsStore
    db = OpsStore(tmp_path / "ops.db")
    with db.tx() as c:
        fid = c.execute("INSERT INTO fade_trades(symbol, session, alert_type, posted_at, channel, message_id, status, "
                        "created_at) VALUES ('KULR','2026-11-05','volume_spike','2026-11-05T15:11:54+00:00','-1',1,"
                        "'filtered','x')").lastrowid
        c.execute("INSERT INTO long_trades(fade_id, symbol, session, kind, status, stop, target, weight, entry_ts, "
                  "est_entry, est_exit, est_exit_at, net, created_at, entry, exit, fill_ts, exit_ts) VALUES "
                  "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (fid, "KULR", "2026-11-05", "L4b", "settled", 3.9, 4.2, 0.05, "2026-11-05T15:13:00+00:00", 4.0, 4.2,
                   "2026-11-05T15:20:00+00:00", 0.031, "x", 4.01, 4.19, "2026-11-05T15:14:00+00:00",
                   "2026-11-05T15:21:00+00:00"))
        c.execute("INSERT INTO long_shadow(fade_id, symbol, session, kind, side, entry, exit, entry_ts, exit_ts, net, "
                  "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)", (fid, "KULR", "2026-11-05", "L4b", "long", 3.50, 4.90,
                  "2026-11-05T15:30:00+00:00", "2026-11-05T15:40:00+00:00", 0.4, "x"))   # never the official prices
    (r,) = tc.long_rows(db)
    assert (r["entry"], r["cover"], r["est_exit_at"], r["setup"]) == (4.01, 4.19, "2026-11-05T15:21:00+00:00",
                                                                       "red-candle break")
    assert r["usd"] == pytest.approx(0.031 * 0.05 * 100_000)
    v = charts.trade_view(r, bars(), r["usd"], side="long")
    assert v["official"] and v["entry"] == 4.01 and v["exit"] == 4.19 and v["ret"] == pytest.approx(0.031)
