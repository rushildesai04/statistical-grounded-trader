"""The long book replayed as the bot (scripts/long_bot_replay.py): the real tick loop and the real settle on one alert."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import long_bot_replay as br  # noqa: E402

from sgt.fast import fade, long_book as lb, patterns as pt, trader_rule  # noqa: E402

POSTED = "2026-11-09 15:00:20+00:00"                             # 10:00:20 ET -> start bar 15:01
T1 = pd.Timestamp("2026-11-09 15:01", tz="UTC")
SPIKE, QUIET, BREAK = (10.0, 10.5, 10.0, 10.4, 900), (10.35, 10.45, 10.25, 10.35, 100), (10.4, 10.65, 10.4, 10.6, 500)
FILL, RUN, AFTER = (10.6, 10.7, 10.55, 10.65, 300), (10.65, 11.4, 10.6, 11.35, 800), (11.3, 11.4, 11.2, 11.3, 100)
RED = (10.5, 10.55, 10.2, 10.3, 400)                             # the first red candle after the alert (the old entry)
EV = {"symbol": "ABCD", "session": "2026-11-09", "posted_at": POSTED, "split": "t"}
COST = 0.01


def bars(rows, start=T1):
    return pd.DataFrame([{"ts": start + i * pt.BAR, "open": o, "high": h, "low": lo, "close": c, "volume": v}
                         for i, (o, h, lo, c, v) in enumerate(rows)])


DAY = bars([SPIKE, RED] + [QUIET] * 10 + [BREAK, FILL, RUN] + [AFTER] * 60)


def cost(sym, ts, px):
    return COST


def test_the_new_book_buys_the_base_break_and_the_old_one_the_red_candle_on_the_same_alert(tmp_path):
    new = br.one(("new", EV, DAY, DAY), tmp_path, cost)
    assert (new["status"], new["kind"], new["rules"], new["est_entry"], new["exit_reason"]) == ("settled", "L8", "B1", 10.6, "target")
    assert new["net"] == pytest.approx(2 * (10.6 - 10.25) / 10.6 - COST) and new["texts"] == 2 and new["armed"] == 0
    assert new["risk"] == pytest.approx((10.6 - 10.25) / 10.6) and 0 < new["weight"] <= lb.PROBE_USD / fade.NAV0
    old = br.one(("old", EV, DAY, DAY), tmp_path, cost)
    assert (old["status"], old["rules"]) == ("settled", "A2") and old["kind"] in ("L4a", "L4b") and old["entry_ts"] < new["entry_ts"]
    assert list(tmp_path.iterdir()) == []                        # the alert's database is removed


def test_the_feed_decides_and_the_tape_pays(tmp_path):
    """The bot buys what the FREE FEED shows; the result is what the OFFICIAL tape did. Here the feed shows a calm
    base and a target; on the tape the fill candle trades down through the stop."""
    tape = DAY.copy()
    tape.loc[13, ["low", "close"]] = [10.20, 10.22]              # official: the fill minute goes through the stop 10.25
    r = br.one(("new", EV, DAY, tape), tmp_path, cost)
    assert (r["kind"], r["reason"], r["exit_reason"]) == ("L8", "target", "stop")       # the phone said target
    assert r["est_exit"] == pytest.approx(11.3) and r["net"] == pytest.approx(10.25 / 10.6 - 1 - COST)


def test_no_trade_and_a_failing_alert_are_rows_and_the_live_settings_are_put_back(tmp_path):
    before = (lb.LIVE_KINDS, lb.LIVE_WATCH, pt.BASE_MAX_RANGE, trader_rule.track, lb.matrix_line)
    flat = bars([(10.0, 10.1, 9.9, 10.0, 100)] * 130)
    none = br.one(("new", EV, flat, flat), tmp_path, cost)
    assert none["status"] in ("none", "norow") and none["net"] is None
    bad = br.one(("new", {**EV, "session": "not-a-date"}, DAY, DAY), tmp_path, cost)
    assert bad["status"].startswith("ERROR") and (bad["symbol"], bad["cfg"]) == ("ABCD", "new")
    with br.configured("new8"):
        assert pt.BASE_MAX_RANGE == 0.08 and lb.LIVE_KINDS == ("L8", "L5v2")
    with br.configured("old"):
        assert lb.LIVE_KINDS == br.LEGACY and lb.LIVE_WATCH == pt.WATCH
    assert (lb.LIVE_KINDS, lb.LIVE_WATCH, pt.BASE_MAX_RANGE, trader_rule.track, lb.matrix_line) == before


def test_the_width_cap_is_what_separates_new_from_new8(tmp_path):
    wide = (10.35, 10.45, 9.95, 10.35, 100)                      # a base 5.0% wide: inside 8%, outside the live 4%
    day = bars([SPIKE, RED] + [wide] * 10 + [BREAK, FILL, RUN] + [AFTER] * 60)
    assert br.one(("new8", EV, day, day), tmp_path, cost)["kind"] == "L8"
    assert br.one(("new", EV, day, day), tmp_path, cost)["status"] in ("none", "norow")


def test_summary_and_alert_jobs(tmp_path):
    rows = [br.one((c, EV, DAY, DAY), tmp_path, cost) for c in ("old", "new")]
    lines = br.summary(pd.DataFrame(rows))
    assert lines[0] == "=== t: 1 alerts" and any(x.startswith("  new   trades     1 | avg +5.60%") for x in lines)
    assert any(x.startswith("  same alerts, old and new both trade: 1") for x in lines)
    d = tmp_path / "bars"
    d.mkdir()
    for feed in ("iex", "sip"):
        DAY.to_parquet(d / f"{feed}_ABCD_2026-11-09.parquet", index=False)
    alerts = [{"symbol": "ABCD", "session": "2026-11-09", "posted_at": POSTED}, {"symbol": "NOPE", "session": "2026-11-09", "posted_at": POSTED},
              {"symbol": 5}, "junk"]
    jobs = list(br.jobs_alerts(alerts, d, ["old", "new"]))
    assert [(j[0], j[1]["symbol"]) for j in jobs] == [("old", "ABCD"), ("new", "ABCD")] and len(jobs[0][2]) == len(DAY)
