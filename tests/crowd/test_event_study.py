"""Event-study entry rule, horizons, MFE/MAE, missing handling, deterministic CI."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sgt.core.types import Bar, Segment
from sgt.crowd.event_study import AlertRecord, run_event_study

ET = ZoneInfo("America/New_York")
START = datetime(2026, 10, 9, 18, 0, tzinfo=timezone.utc)  # 14:00 ET


def _bar(symbol: str, ts: datetime, close: float, *, high=None, low=None) -> Bar:
    et = ts.astimezone(ET)
    hi = high if high is not None else close
    lo = low if low is not None else close
    return Bar(
        symbol=symbol, ts=ts, open=close, high=max(close, hi), low=min(close, lo),
        close=close, volume=1000.0, session_date=et.date(),
        minute_of_day=et.hour * 60 + et.minute, segment=Segment.RTH,
    )


def _series(symbol: str, closes: list[float], *, start=START, highs=None, lows=None) -> list[Bar]:
    bars = []
    for i, c in enumerate(closes):
        bars.append(_bar(
            symbol, start + timedelta(minutes=i), c,
            high=None if highs is None else highs[i],
            low=None if lows is None else lows[i],
        ))
    return bars


def test_entry_is_next_completed_bar_after_midbar_alert():
    # closes at minute offsets 0..5 -> prices 10,11,12,13,14,15
    bars = _series("YMAT", [10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    # Alert fired mid the second bar (offset +1 => 18:01), at 18:01:30.
    alert = AlertRecord("YMAT", "new_high", START + timedelta(minutes=1, seconds=30))
    res = run_event_study([alert], {"YMAT": bars}, horizons=(1, 2))
    # Entry = close of first bar opening strictly after 18:01:30 => offset 2 (12.0).
    assert res.n_with_entry == 1
    assert res.overall.horizons[1].mean == (13.0 - 12.0) / 12.0
    assert res.overall.horizons[2].mean == (14.0 - 12.0) / 12.0


def test_alert_at_exact_bar_open_uses_following_bar():
    bars = _series("YMAT", [10.0, 11.0, 12.0, 13.0])
    # Alert exactly at the 18:01 bar open -> not strictly after -> entry at 18:02 (12.0).
    alert = AlertRecord("YMAT", "new_high", START + timedelta(minutes=1))
    res = run_event_study([alert], {"YMAT": bars}, horizons=(1,))
    assert res.overall.horizons[1].mean == (13.0 - 12.0) / 12.0


def test_horizon_out_of_range_not_counted():
    bars = _series("YMAT", [10.0, 11.0, 12.0])  # entry at offset2=12.0, no bar after
    alert = AlertRecord("YMAT", "new_high", START + timedelta(minutes=1, seconds=30))
    res = run_event_study([alert], {"YMAT": bars}, horizons=(1, 5))
    assert res.overall.horizons[1].n == 0
    assert res.overall.horizons[5].n == 0
    assert res.n_with_entry == 1  # entry existed, just no forward bar


def test_mfe_mae():
    # entry at offset2 (close 12.0). Later bars carry a high spike and a low dip.
    closes = [10.0, 11.0, 12.0, 12.5, 12.0]
    highs = [10.0, 11.0, 12.0, 18.0, 12.0]  # MFE from the 4th bar
    lows = [10.0, 11.0, 12.0, 12.5, 6.0]    # MAE from the 5th bar
    bars = _series("YMAT", closes, highs=highs, lows=lows)
    alert = AlertRecord("YMAT", "new_high", START + timedelta(minutes=1, seconds=30))
    res = run_event_study([alert], {"YMAT": bars}, horizons=(1,))
    assert res.overall.mfe_mean == (18.0 - 12.0) / 12.0
    assert res.overall.mae_mean == (6.0 - 12.0) / 12.0


def test_missing_bars_counted_not_dropped():
    bars = _series("YMAT", [10.0, 11.0, 12.0, 13.0])
    alerts = [
        AlertRecord("YMAT", "new_high", START + timedelta(minutes=1, seconds=30)),
        AlertRecord("NOPE", "new_high", START + timedelta(minutes=1, seconds=30)),  # no bars
    ]
    res = run_event_study(alerts, {"YMAT": bars}, horizons=(1,))
    assert res.n_alerts == 2
    assert res.missing == 1
    assert res.n_with_entry == 1


def test_halted_within_60m_flag():
    bars = _series("WFF", [10.0] * 70)
    alerts = [
        AlertRecord("WFF", "new_high", START + timedelta(minutes=1)),
        AlertRecord("WFF", "halted", START + timedelta(minutes=10)),  # within 60m
    ]
    res = run_event_study(alerts, {"WFF": bars}, horizons=(1,))
    grp = res.groups["alert_type=new_high"]
    assert grp.halted_rate == 1.0


def test_latency_metric():
    # 30 bars so there is a bar ~15 min before the alert.
    bars = _series("YMAT", [10.0 + i for i in range(30)])
    # Alert at offset 20 (mid bar). Entry = offset 21 close. 15m before = offset ~5.
    alert = AlertRecord("YMAT", "new_high", START + timedelta(minutes=20, seconds=30))
    res = run_event_study([alert], {"YMAT": bars}, horizons=(1,))
    assert res.overall.latency_mean > 0  # price already climbed before entry


def test_grouping_by_news_and_session_and_float():
    bars = _series("YMAT", [10.0, 11.0, 12.0, 13.0, 14.0])
    alerts = [
        AlertRecord("YMAT", "new_high", START + timedelta(minutes=1, seconds=30), has_news=True),
        AlertRecord("YMAT", "halted", START + timedelta(minutes=1, seconds=30), has_news=False),
    ]
    res = run_event_study(
        alerts, {"YMAT": bars}, horizons=(1,), float_bucket={"YMAT": "low"}
    )
    assert "has_news=yes" in res.groups
    assert "has_news=no" in res.groups
    assert "float_bucket=low" in res.groups
    assert any(k.startswith("session=") for k in res.groups)


def test_deterministic_bootstrap_ci():
    bars = _series("YMAT", [10.0, 11.0, 12.0, 13.0, 9.0, 14.0, 8.0, 15.0])
    alerts = [
        AlertRecord("YMAT", "new_high", START + timedelta(minutes=i, seconds=30))
        for i in range(0, 4)
    ]
    r1 = run_event_study(alerts, {"YMAT": bars}, horizons=(1,))
    r2 = run_event_study(alerts, {"YMAT": bars}, horizons=(1,))
    s1, s2 = r1.overall.horizons[1], r2.overall.horizons[1]
    assert s1.ci_low == s2.ci_low
    assert s1.ci_high == s2.ci_high
    assert s1.n >= 2


def test_to_markdown_runs():
    bars = _series("YMAT", [10.0, 11.0, 12.0, 13.0])
    alert = AlertRecord("YMAT", "new_high", START + timedelta(minutes=1, seconds=30))
    md = run_event_study([alert], {"YMAT": bars}, horizons=(1,)).to_markdown()
    assert "Telegram alert event study" in md
    assert "ret_1m" in md
