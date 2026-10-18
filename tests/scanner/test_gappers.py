"""Gap-day detection uses only open vs previous close (no look-ahead)."""
from __future__ import annotations

from datetime import date

from sgt.scanner.gappers import DailyBar, find_gap_days, parse_daily_rows


def _b(d, o, c, v=1e6):
    return DailyBar(date(2026, 9, d), o, c, v)


def test_gap_detected_from_open_vs_prev_close():
    bars = [_b(1, 4.0, 5.0), _b(2, 5.6, 4.0), _b(3, 4.1, 9.0)]
    got = find_gap_days("abc", bars)
    assert [(g.session.day, g.gap_pct) for g in got] == [(2, 12.0)]
    assert got[0].symbol == "ABC" and got[0].prev_close == 5.0


def test_day_high_or_close_never_used():
    # Day 3 closes +120% but opened flat: not a gap day (that would be look-ahead).
    bars = [_b(1, 4.0, 4.0), _b(2, 4.0, 4.0), _b(3, 4.05, 8.8)]
    assert find_gap_days("X", bars) == []


def test_price_band_and_threshold_and_start():
    bars = [_b(1, 0.5, 0.5), _b(2, 0.8, 0.8), _b(3, 50, 40), _b(4, 60, 60), _b(5, 10, 10),
            _b(6, 10.9, 11)]
    assert find_gap_days("X", bars) == []                       # sub-$1 / >$30 / 9% < 10%
    assert [g.session.day for g in find_gap_days("X", bars, min_gap=0.08)] == [6]
    assert find_gap_days("X", bars, min_gap=0.08, start=date(2026, 9, 7)) == []


def test_parse_rejects_malformed_rows():
    rows = [{"t": "2026-09-02T04:00:00Z", "o": 5, "c": 6, "v": 10},
            {"t": "2026-09-01T04:00:00Z", "o": 4, "c": 5, "v": 10},
            {"t": "bad", "o": 1, "c": 1, "v": 1}, {"o": 1}, {"t": "2026-09-03", "o": -1, "c": 1, "v": 1},
            {"t": "2026-09-04", "o": "nan", "c": 1, "v": 1}]
    got = parse_daily_rows(rows)
    assert [b.session.day for b in got] == [1, 2]
