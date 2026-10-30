"""Entry/exit timing of scripts/telegram_alert_test.py (prereg 2026-10-29)."""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import telegram_alert_test as tat  # noqa: E402

from types import SimpleNamespace  # noqa: E402

D = date(2025, 3, 3)
T0 = datetime(2025, 3, 3, 15, 0, tzinfo=timezone.utc)  # 10:00 ET


class FakeStore:
    def __init__(self, bars):
        self.bars = bars

    def read(self, symbol, start, end):
        return self.bars


def _bars(n=90, skip=()):
    out = []
    for m in range(n):
        if m in skip:
            continue
        px = 2.0 + m * 0.01
        out.append(SimpleNamespace(ts=T0 + timedelta(minutes=m), open=px, close=px + 0.005))
    return out


def _alert(posted):
    return pd.DataFrame({"symbol": ["ABCD"], "alert_type": ["volume_spike"], "posted_at": [pd.Timestamp(posted)],
                         "has_news": [None], "session": [D]})


def test_entry_waits_10s_and_uses_next_open():
    t = tat.attach_trades(_alert(T0 + timedelta(minutes=5, seconds=55)), FakeStore(_bars()))
    r = t.iloc[0]
    assert r.status == "ok"
    assert r.entry_ts == pd.Timestamp(T0 + timedelta(minutes=7))  # :55 + 10 s -> 2 bars later
    assert r.entry == pytest.approx(2.07)
    assert r.gross_15 == pytest.approx(2.22 / 2.07 - 1)  # open of the bar 15 min after entry


def test_entry_at_next_minute_when_early():
    r = tat.attach_trades(_alert(T0 + timedelta(minutes=5, seconds=20)), FakeStore(_bars())).iloc[0]
    assert r.entry_ts == pd.Timestamp(T0 + timedelta(minutes=6))


def test_halt_gap_exits_at_first_bar_after():
    r = tat.attach_trades(_alert(T0 + timedelta(seconds=5)), FakeStore(_bars(skip=range(10, 30)))).iloc[0]
    assert r.entry_ts == pd.Timestamp(T0 + timedelta(minutes=1))
    assert r.gross_15 == pytest.approx(2.30 / 2.01 - 1)  # first bar >= 16 min is minute 30


def test_no_entry_when_first_bar_too_late():
    r = tat.attach_trades(_alert(T0 + timedelta(minutes=2)), FakeStore(_bars(skip=range(0, 10)))).iloc[0]
    assert r.status == "no_entry"


def test_no_bars():
    assert tat.attach_trades(_alert(T0), FakeStore([])).iloc[0].status == "no_bars"


def test_net_subtracts_round_trip_cost():
    r = tat.attach_trades(_alert(T0 + timedelta(seconds=5)), FakeStore(_bars())).iloc[0]
    assert 0.005 < r.cost_rt < 0.03
    assert r.net_15 == pytest.approx(r.gross_15 - r.cost_rt)
