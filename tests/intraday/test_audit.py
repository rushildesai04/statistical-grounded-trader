"""Data audit metrics and source comparison."""
from __future__ import annotations

from datetime import date, datetime

from sgt.core.types import Segment
from sgt.intraday.audit import audit_source, compare_sources
from sgt.intraday.calendar import default_calendar
from sgt.intraday.synthetic import make_session_bars

D = date(2025, 3, 14)


class _FakeSource:
    name = "fake"

    def __init__(self, bars_by_symbol):
        self._bars = bars_by_symbol

    def fetch(self, symbol, start, end):
        return list(self._bars.get(symbol, []))


def test_audit_full_rth_coverage():
    bars = make_session_bars("WFF", D, segments=(Segment.RTH,), seed=1)
    src = _FakeSource({"WFF": bars})
    rep = audit_source(src, ["WFF"], D, D)
    s = rep.per_symbol[0]
    assert s.symbol == "WFF"
    assert s.sessions_covered == 1
    assert s.extended_hours_share == 0.0
    assert s.rth_missing_minute_rate == 0.0
    assert s.n_bars == 390  # 6.5h RTH


def test_audit_missing_minute_rate():
    bars = make_session_bars("WFF", D, segments=(Segment.RTH,), seed=1)
    kept = bars[:-39]  # drop last 39 RTH minutes
    src = _FakeSource({"WFF": kept})
    rep = audit_source(src, ["WFF"], D, D)
    s = rep.per_symbol[0]
    assert abs(s.rth_missing_minute_rate - 39 / 390) < 1e-9


def test_audit_extended_hours_share():
    bars = make_session_bars("WFF", D, segments=(Segment.PRE, Segment.RTH, Segment.POST), seed=1)
    src = _FakeSource({"WFF": bars})
    rep = audit_source(src, ["WFF"], D, D)
    s = rep.per_symbol[0]
    ext = sum(1 for b in bars if b.segment in (Segment.PRE, Segment.POST))
    assert abs(s.extended_hours_share - ext / len(bars)) < 1e-9
    assert 0.0 < s.extended_hours_share < 1.0


def test_audit_empty_symbol():
    src = _FakeSource({})
    rep = audit_source(src, ["ZZZ"], D, D)
    s = rep.per_symbol[0]
    assert s.n_bars == 0 and s.first_bar is None and s.rth_missing_minute_rate == 0.0


def test_audit_to_markdown():
    bars = make_session_bars("WFF", D, segments=(Segment.RTH,), seed=1)
    rep = audit_source(_FakeSource({"WFF": bars}), ["WFF"], D, D)
    md = rep.to_markdown()
    assert "# Data audit" in md and "WFF" in md and "|" in md


def test_compare_sources_agreement():
    cal = default_calendar()
    ts = datetime(2025, 3, 14, 14, 31, tzinfo=__import__("datetime").timezone.utc)
    a = cal.annotate("WFF", ts, 5.0, 5.2, 4.8, 5.10, 1000.0)
    b = cal.annotate("WFF", ts, 5.0, 5.2, 4.8, 5.11, 1050.0)  # close within 0.5%, vol within 10%
    rep = compare_sources([a], [b])
    assert rep.n_overlap == 1
    assert rep.close_agree_pct == 1.0
    assert rep.volume_agree_pct == 1.0
    assert rep.close_mean_abs_pct < 0.5


def test_compare_sources_disagreement_and_no_overlap():
    cal = default_calendar()
    tz = __import__("datetime").timezone.utc
    ts = datetime(2025, 3, 14, 14, 31, tzinfo=tz)
    a = cal.annotate("WFF", ts, 5.0, 5.2, 4.8, 5.0, 1000.0)
    b = cal.annotate("WFF", ts, 5.0, 6.0, 4.8, 5.9, 5000.0)  # ~18% close, big vol gap
    rep = compare_sources([a], [b])
    assert rep.close_agree_pct == 0.0 and rep.volume_agree_pct == 0.0
    # no overlapping minutes
    other = cal.annotate("WFF", datetime(2025, 3, 14, 14, 32, tzinfo=tz), 5, 5.2, 4.8, 5, 10)
    assert compare_sources([a], [other]).n_overlap == 0


def test_zero_volume_share_per_segment_is_reported():
    """F-2 regression: yfinance-style zero-volume extended-hours bars must be visible."""
    from datetime import date

    from sgt.core.types import Bar, Segment
    from sgt.intraday.audit import _audit_symbol
    from sgt.intraday.calendar import default_calendar
    from sgt.intraday.synthetic import make_session_bars

    bars = make_session_bars("ZVOL", date(2026, 10, 12), segments=(Segment.PRE, Segment.RTH, Segment.POST), seed=3)
    zeroed = [
        Bar(symbol=b.symbol, ts=b.ts, open=b.open, high=b.high, low=b.low, close=b.close,
            volume=0.0 if b.segment is not Segment.RTH else b.volume,
            session_date=b.session_date, minute_of_day=b.minute_of_day, segment=b.segment)
        for b in bars
    ]
    audit = _audit_symbol("ZVOL", zeroed, default_calendar())
    assert audit.zero_volume_share_pre == 1.0
    assert audit.zero_volume_share_post == 1.0
    assert audit.zero_volume_share_rth < 0.05


def test_compare_sources_zero_vs_nonzero_volume_is_disagreement():
    """R-4 regression: zero volume on one side must not count as agreement."""
    from datetime import date

    from sgt.core.types import Bar
    from sgt.intraday.audit import compare_sources
    from sgt.intraday.synthetic import make_session_bars

    a = make_session_bars("CMP", date(2026, 10, 12), seed=5)[:10]
    b = [Bar(symbol=x.symbol, ts=x.ts, open=x.open, high=x.high, low=x.low, close=x.close, volume=0.0,
             session_date=x.session_date, minute_of_day=x.minute_of_day, segment=x.segment) for x in a]
    rep = compare_sources(a, b)
    assert rep.n_overlap == 10
    assert rep.volume_agree_pct == 0.0
