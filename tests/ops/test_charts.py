from __future__ import annotations

import json
import re
from datetime import datetime, timezone

import pandas as pd
import pytest

from sgt.fast import est_costs
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
    cost = est_costs.cost("short", TRADE["symbol"], TRADE["est_entry_at"], 4.465)       # 2026-11-16: estimate after costs
    assert v["stop"] == pytest.approx(4.465 * 1.2) and v["ret"] == pytest.approx((4.465 - 4.64) / 4.465 - cost)
    assert v["ret"] == pytest.approx(-0.0567, abs=5e-5)                                 # -3.9% move, 1.75% costs
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
    assert v["ret"] == pytest.approx(0.05 - est_costs.cost("long", "KULR", LONG["est_entry_at"], 4.0))   # after costs
    assert v["ret"] == pytest.approx(0.0375, abs=5e-5)                   # long: exit / entry - 1, less 1.25%
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


def test_card_dollars_of_a_live_estimate_are_after_estimated_costs(tmp_path):
    """2026-11-16: a card's dollars before the settle are the move less the estimated costs, on both sides; once
    settled they are the official net and nothing more is taken off."""
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
                  "est_entry, est_exit, est_exit_at, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (fid, "KULR", "2026-11-05", "L4b", "est_closed", 3.9, 4.2, 0.05, "2026-11-05T15:13:00+00:00", 4.0, 4.2,
                   "2026-11-05T15:20:00+00:00", "x"))
    (r,) = tc.long_rows(db)
    long_cost = est_costs.cost("long", "KULR", "2026-11-05T15:13:00+00:00", 4.0)
    assert r["usd"] == pytest.approx((0.05 - long_cost) * 0.05 * 100_000) and r["usd"] < 0.05 * 0.05 * 100_000
    short_cost = est_costs.cost("short", "DNUT", TRADE["est_entry_at"], 4.465)
    assert short_cost > long_cost                                         # a short also pays the locate
    assert tc.dollars(TRADE, None) == pytest.approx(((4.465 - 4.64) / 4.465 - short_cost) * 0.05 * 100_000)
    assert tc.dollars({**TRADE, "net": -0.02}, None) == pytest.approx(-0.02 * 0.05 * 100_000)   # official: as booked
    assert tc.dollars({**TRADE, "est_exit": None}, None) is None          # still open: no dollars invented


def test_view_says_where_the_candles_end_and_when_the_exit_was():
    """Owner note on CISS 2026-11-10: the chart ended at 10:24 but the exit was 10:32 (intraday bars lag ~16 min).
    The view carries both so the page can say so instead of looking like a missing exit."""
    v = charts.trade_view(TRADE, bars(n=70))                     # bars to 15:39 UTC, exit 16:13 UTC
    assert v["exit_at"] == charts.et_epoch("2026-11-05T16:13:00Z")
    assert v["bars_end"] == charts.et_epoch("2026-11-05T15:39:00Z") and v["exit_at"] - v["bars_end"] > 90
    full = charts.trade_view(TRADE, bars())
    assert full["bars_end"] >= full["exit_at"] - 60
    assert charts.trade_view({**TRADE, "est_exit_at": None}, bars())["exit_at"] is None


def test_a_number_that_is_not_finite_is_shown_blank_and_never_stops_the_page():
    # audit 2026-11-15: json has no NaN / infinity; one such number in one card could stop the whole page building
    from sgt.ops import charts as ch

    got = ch.finite({"a": float("nan"), "b": [1.5, float("inf"), {"c": -float("inf"), "d": "nan"}], "e": (2, None), "f": True})
    assert got == {"a": None, "b": [1.5, None, {"c": None, "d": "nan"}], "e": [2, None], "f": True}
    page = ch.render([], datetime(2026, 11, 13, 21, 0, tzinfo=timezone.utc),
                     book_views=[{"symbol": "AMD", "usd": float("nan"), "score": {"usd": float("inf"), "go": True}}])
    data = json.loads(re.search(r"const DATA = (\{.*?\});\n", page, re.S).group(1))
    assert data["book_views"] == [{"symbol": "AMD", "usd": None, "score": {"usd": None, "go": True}}]


def test_the_page_build_leaves_off_the_oldest_sessions_to_stay_under_its_budget():
    """2026-11-17: the page had grown to 6.0 MB against the server's 8 MB limit; over it the whole site goes dark."""
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    import trade_charts as tc

    card = lambda day, n=1000: {"session": day, "blob": "x" * n}  # noqa: E731
    cards = {"views": [card("2026-11-13"), card("2026-11-17")], "scan_views": [card("2026-11-10")],
             "book_views": [card("2026-11-10"), card("2026-11-12"), card("2026-11-17"), {"blob": "y" * 10}]}
    calls = []

    def render(views, scan_views, book_views):
        calls.append((len(views), len(scan_views), len(book_views)))
        return "".join(v["blob"] for v in views + scan_views + book_views)

    html, dropped = tc.fit(render, cards, budget=10_000)
    assert dropped == [] and len(html) == 6010 and calls == [(2, 1, 4)]                 # under budget: untouched
    html, dropped = tc.fit(render, cards, budget=3500)
    assert dropped == ["2026-11-10", "2026-11-12"] and len(html) == 3010                # oldest sessions first
    assert cards["scan_views"] and len(cards["book_views"]) == 4                          # the caller's lists are not changed
    html, dropped = tc.fit(render, cards, budget=5)                                       # nothing left to drop
    assert dropped == ["2026-11-10", "2026-11-12", "2026-11-13", "2026-11-17"] and html == "y" * 10
    assert tc.PAGE_BUDGET < 8 * 1024 * 1024                                               # under the server's limit
    from sgt.ops import chartserver
    assert tc.PAGE_BUDGET < chartserver.MAX_PAGE


# ---- audit 2026-11-21: a settled card shows the OFFICIAL exit (time and reason), not the live feed's ----
SETTLED = {**TRADE, "status": "settled", "net": -0.045, "entry": 4.0, "cover": 4.1, "entry_ts": "2026-11-05 15:13:00+00:00"}


def path(marks: dict):
    """Flat $4 bars with chosen highs: minute offset from 14:30 UTC -> high."""
    b = bars()
    for m, h in marks.items():
        b.loc[m, "high"] = h
    return b


def test_settled_short_that_officially_held_shows_the_60_minute_exit_not_the_live_stop():
    # FISN 2026-11-09: the free feed texted a stop; the official tape never reached entry x 1.2
    t = {**SETTLED, "reason": "stop", "est_exit_at": "2026-11-05T15:30:00+00:00"}
    v = charts.trade_view(t, bars())
    assert v["reason"] == "60 min" and v["live_reason"] == "stop"
    assert v["exit_at"] == charts.et_epoch("2026-11-05T16:12:00Z")       # the last of the 60 official minutes from 15:13
    assert v["markers"][2]["time"] == charts.et_epoch("2026-11-05T16:12:00Z")


def test_settled_short_that_officially_stopped_shows_the_stop_minute_not_the_live_timer():
    # MPU 2026-11-17: the free feed held to its timer; the official tape crossed the stop at 15:40
    t = {**SETTLED, "cover": 4.8, "net": -0.2175, "reason": "60 min"}
    v = charts.trade_view(t, path({70: 4.85}))                             # 14:30 + 70 min = 15:40
    assert v["reason"] == "stop" and v["live_reason"] == "60 min"
    assert v["exit_at"] == charts.et_epoch("2026-11-05T15:40:00Z") and v["exit"] == 4.8


def test_a_late_seen_stop_and_a_plain_stop_are_the_same_exit():
    t = {**SETTLED, "cover": 4.8, "reason": "stop (official tape, seen late)"}
    v = charts.trade_view(t, path({70: 4.85}))
    assert v["reason"] == "stop" and v["live_reason"] is None


def test_agreeing_labels_show_once_and_an_estimate_keeps_its_live_exit():
    v = charts.trade_view(SETTLED, bars())
    assert v["reason"] == "60 min" and v["live_reason"] is None
    live = charts.trade_view({**TRADE, "reason": "stop"}, bars())          # not settled: nothing official to show
    assert live["reason"] == "stop" and live["live_reason"] is None
    assert live["exit_at"] == charts.et_epoch(TRADE["est_exit_at"])


def test_the_owners_own_exit_keeps_its_time_and_label():
    t = {**SETTLED, "reason": "your NO", "est_exit_at": "2026-11-05T15:25:10+00:00"}
    v = charts.trade_view(t, bars())
    assert v["reason"] == "your NO" and v["exit_at"] == charts.et_epoch("2026-11-05T15:25:10Z")


def test_a_short_entered_late_in_the_day_is_labelled_close():
    b = bars(start="2026-11-05T20:00:00Z", n=60)                           # the session's last hour
    t = {**SETTLED, "posted_at": "2026-11-05T20:39:00+00:00", "entry_ts": "2026-11-05 20:40:00+00:00",
         "est_entry_at": "2026-11-05T20:40:10+00:00", "est_exit_at": "2026-11-05T21:00:00+00:00", "reason": "close"}
    v = charts.trade_view(t, b)
    assert v["reason"] == "close" and v["exit_at"] == charts.et_epoch("2026-11-05T20:59:00Z")


def test_official_exit_cannot_be_read_without_bars_or_prices():
    b = bars()
    assert charts.official_short_exit(b.iloc[:0], "2026-11-05 15:13:00+00:00", 4.0, 4.1) is None
    assert charts.official_short_exit(b, "2026-11-05 15:13:00+00:00", None, 4.1) is None
    assert charts.official_short_exit(b, "2026-11-05 23:00:00+00:00", 4.0, 4.1) is None      # no bar in the window
    assert charts.official_short_exit(b, "2026-11-05 15:13:00+00:00", 4.0, 4.8) is None      # a stop the bars do not show


def test_settled_long_shows_its_official_exit_reason_and_the_texted_one():
    t = {**LONG, "status": "settled", "net": -0.038, "entry": 4.0, "cover": 3.9, "entry_ts": "2026-11-05 15:14:00+00:00",
         "reason": "target", "exit_reason": "stop"}                        # ADXN 2026-11-12: texted target, official stop
    v = charts.trade_view(t, bars(), side="long")
    assert v["reason"] == "stop" and v["live_reason"] == "target"
    same = charts.trade_view({**t, "reason": "stop"}, bars(), side="long")
    assert same["reason"] == "stop" and same["live_reason"] is None
    est = charts.trade_view({**LONG, "reason": "target", "exit_reason": None}, bars(), side="long")
    assert est["reason"] == "target" and est["live_reason"] is None


def test_a_result_note_is_not_shown_as_a_live_label():
    note = "official at the live entry (not a tested event: no_entry)"
    v = charts.trade_view({**SETTLED, "cover": 4.8, "reason": note}, path({70: 4.85}))
    assert v["reason"] == "stop" and v["live_reason"] is None and v["result_note"] == note
    assert charts.trade_view(SETTLED, bars())["result_note"] is None


def test_the_page_says_the_money_verdict_is_advice_and_shows_the_live_label():
    """Audit 2026-11-21: NO-GO beside a taken trade, and a live exit label on an official result, were unexplained."""
    page = charts.TEMPLATE.read_text()
    assert "Verdict as of this page, not as of the entry" in page
    assert 'fact("texted live", t.live_reason)' in page and 'fact("scored", t.result_note)' in page


def test_a_settled_card_keeps_the_live_paper_path_beside_the_settled_replay():
    """GLXG 2026-11-24: texted +4.7% on the free feed (short 1.02, stop 1.224, out 0.9598), settled -21.2% (short 1.00,
    stop 1.20). The card showed only the settled record. Both are two records of one trade; neither is a broker fill."""
    ts = pd.date_range("2026-11-24 17:50", periods=80, freq="1min", tz="UTC")
    bars = pd.DataFrame({"ts": ts, "open": 1.0, "high": [1.2 if i == 18 else 1.05 for i in range(80)], "low": 0.9,
                         "close": 1.0, "volume": 1000})
    t = {"id": 489, "symbol": "GLXG", "session": "2026-11-24", "alert_type": "gaining_momentum", "status": "settled",
         "reason": "60 min", "posted_at": "2026-11-24T17:57:40+00:00", "est_entry": 1.02,
         "est_entry_at": "2026-11-24T18:00:32.542634+00:00", "est_exit": 0.9598, "est_exit_at": "2026-11-24T19:00:37+00:00",
         "entry_ts": "2026-11-24 17:58:00+00:00", "entry": 1.0, "cover": 1.2, "net": -0.2123386}
    v = charts.trade_view(t, bars)
    assert (v["entry"], v["exit"], v["reason"], v["live_reason"], v["official"]) == (1.0, 1.2, "stop", "60 min", True)
    assert v["stop"] == pytest.approx(1.2) and v["ret"] == pytest.approx(-0.2123386)
    live = v["live"]
    assert (live["entry"], live["exit"], live["revised"]) == (1.02, 0.9598, True)
    assert live["ret"] == pytest.approx(0.0467, abs=1e-4) and live["stop"] == pytest.approx(1.224)
    assert live["entry_at"] == charts.et_epoch("2026-11-24T18:00:32.542634+00:00") and live["exit_at"] is not None
    assert charts.trade_view({**t, "status": "est_closed", "entry": None, "cover": None, "net": None, "entry_ts": None},
                             bars)["live"] is None                           # not settled: the estimate IS the card
    small = charts.trade_view({**t, "est_entry": 1.0, "est_exit": 1.2, "reason": "stop"}, bars)["live"]
    assert small["revised"] is False                                         # shown all the same, just not flagged

