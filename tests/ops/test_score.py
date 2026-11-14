"""Money score (owner 2026-11-14): expected dollars of a kind of trade, the swing, GO / NO-GO."""
from __future__ import annotations

import json

import pytest

from sgt.learning import live_monitor
from sgt.ops import score
from sgt.ops.store import OpsStore


def test_model_calls_one_number_per_side_and_dollars_scale_with_the_position():
    s = score.for_trade("model_long", 13_400, sigma=0.074)
    assert s["go"] is True and s["usd"] == pytest.approx(13_400 * 0.0025) and s["swing"] == pytest.approx(13_400 * 0.074)
    assert s["n"] == 3448 and s["win"] == 0.532
    t = score.for_trade("model_short", -21_900, sigma=0.038)          # a signed size is still dollars at work
    assert t["go"] is False and t["usd"] == pytest.approx(-21_900 * 0.0008) and t["swing"] == pytest.approx(21_900 * 0.038)
    assert score.for_trade("model_long", 13_400)["swing"] is None    # no swing invented without the stock's risk
    json.dumps([s, t], allow_nan=False)


def test_kinds_of_daily_books():
    assert score.kind_of("megacap_long", "long") == score.kind_of("megacap_ext", "long") == "model_long"
    assert score.kind_of("megacap", "short") == "model_short"
    assert score.kind_of("megacap_overlay", "long") == score.kind_of("etf:B_6040", "long") == "yardstick"
    y = score.for_trade("yardstick", 60_000, sigma=0.01)
    assert y["go"] is None and y["usd"] is None and score.line(y) is None       # a yardstick is not a call


def test_alert_kinds_quote_the_same_expectation_their_monitor_judges():
    long = score.for_trade("alert_long", 4_956)
    book = next(b for b in live_monitor.BOOKS if b.name == "long book (paper)")
    assert long["ev"] == book.expectancy == -0.0209 and long["go"] is False
    assert long["usd"] == pytest.approx(-0.0209 * 4_956) and long["swing"] == pytest.approx(book.std * 4_956)
    assert score.line(long) == "expected -$104 (swing about $310): NO-GO"
    fade = score.for_trade("fade", 4_823)
    assert fade["ev"] == 0.0170 and fade["go"] is True              # no db: the tested figure


def test_the_fade_uses_its_forward_record_while_the_guard_is_in_alarm(tmp_path, monkeypatch):
    db = OpsStore(tmp_path / "ops.db")
    monkeypatch.setattr(live_monitor, "check_forward", lambda d: {"alarms": ["win rate"], "n": 73, "mean": -0.018, "win": 0.45})
    s = score.for_trade("fade", 5_000, db=db)
    assert s["go"] is False and s["ev"] == -0.018 and s["usd"] == pytest.approx(-90) and s["n"] == 73
    assert "drift guard in alarm" in s["basis"] and "tested +1.7%" in s["basis"]
    monkeypatch.setattr(live_monitor, "check_forward", lambda d: {"alarms": [], "n": 73, "mean": -0.018, "win": 0.45})
    assert score.for_trade("fade", 5_000, db=db)["ev"] == 0.0170                 # no alarm: tested
    monkeypatch.setattr(live_monitor, "check_forward", lambda d: (_ for _ in ()).throw(OSError("unreadable")))
    assert score.for_trade("fade", 5_000, db=db)["ev"] == 0.0170                 # unreadable: tested, never better


def test_unknown_kind_or_size_is_never_a_silent_go():
    u = score.for_trade("mystery", 1_000)
    assert u["go"] is None and u["usd"] is None and score.line(u) is None
    n = score.for_trade("model_long", None, sigma=0.02)
    assert n["usd"] is None and n["swing"] is None and n["go"] is True and score.line(n) == "expected +0.25% per trade: GO"


def test_resize_keeps_the_kind_and_rescales_the_dollars():
    one = score.for_trade("fade", None)
    assert one["usd"] is None and one["swing"] is None and one["go"] is True
    s = score.resize(one, 4_823)
    assert s["usd"] == pytest.approx(0.0170 * 4_823) and s["swing"] == pytest.approx(0.1735 * 4_823) and s["size"] == 4_823
    assert score.resize(one, None)["usd"] is None and one["usd"] is None           # the original is not changed
