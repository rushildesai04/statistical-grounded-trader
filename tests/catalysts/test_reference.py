"""Point-in-time reference data: filed-date gating, dilution lookback, float override."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from sgt.catalysts.models import FloatOverrideRow, SecFilingRow, SharesSnapshotRow
from sgt.catalysts.reference import FLOAT_PROXY_SOURCE, ReferenceDataProvider
from sgt.db.session import get_session

UTC = timezone.utc


def _dt(y, m, d):
    return datetime(y, m, d, tzinfo=UTC)


@pytest.fixture
def seeded():
    with get_session() as s:
        s.add(SharesSnapshotRow(ticker="WFF", period_end=_dt(2026, 6, 30),
                                filed_date=_dt(2026, 7, 15), source="edgar_dei", value=10_000_000))
        s.add(SharesSnapshotRow(ticker="WFF", period_end=_dt(2026, 9, 30),
                                filed_date=_dt(2026, 10, 20), source="edgar_dei", value=12_500_000))
        s.add(SecFilingRow(accession="a-1", cik=320193, ticker="WFF", form="S-3",
                           filed_at=_dt(2026, 9, 1)))


def test_shares_outstanding_is_point_in_time(seeded):
    p = ReferenceDataProvider()
    # on 2026-09-01 only the 2026-07-15 filing is knowable
    r = p.get("WFF", date(2026, 9, 1))
    assert r.shares_outstanding == 10_000_000
    # after the later filing is public
    r2 = p.get("WFF", date(2026, 10, 25))
    assert r2.shares_outstanding == 12_500_000


def test_snapshot_filed_after_as_of_excluded(seeded):
    p = ReferenceDataProvider()
    r = p.get("WFF", date(2026, 10, 19))  # day before the 12.5M filing
    assert r.shares_outstanding == 10_000_000


def test_free_float_falls_back_to_outstanding_proxy(seeded):
    r = ReferenceDataProvider().get("WFF", date(2026, 9, 1))
    assert r.free_float == 10_000_000
    assert r.float_source == FLOAT_PROXY_SOURCE


def test_float_override_takes_precedence_when_effective(seeded):
    with get_session() as s:
        s.add(FloatOverrideRow(ticker="WFF", effective_date=_dt(2026, 8, 1),
                               free_float=3_000_000, source="manual", note="insider lockup"))
    p = ReferenceDataProvider()
    r = p.get("WFF", date(2026, 9, 1))
    assert r.free_float == 3_000_000 and r.float_source == "manual"
    # before the override is effective -> proxy again
    r0 = p.get("WFF", date(2026, 7, 20))
    assert r0.free_float == 10_000_000 and r0.float_source == FLOAT_PROXY_SOURCE


def test_dilution_flag_within_lookback(seeded):
    p = ReferenceDataProvider(lookback_days=90)
    assert p.get("WFF", date(2026, 9, 15)).dilution_flag is True   # 14 days after S-3
    assert p.get("WFF", date(2026, 12, 15)).dilution_flag is False  # >90 days later


def test_dilution_flag_excludes_future_filing(seeded):
    p = ReferenceDataProvider()
    assert p.get("WFF", date(2026, 8, 15)).dilution_flag is False  # before the S-3 was filed


def test_unknown_symbol_returns_empty(seeded):
    r = ReferenceDataProvider().get("ZZZ", date(2026, 9, 1))
    assert r.shares_outstanding is None and r.free_float is None
    assert r.float_source == "" and r.dilution_flag is False


def test_same_day_intraday_filing_not_visible_before_acceptance():
    """R-1 regression: an S-3 accepted 15:00 ET must not be visible to a 09:35 decision."""
    accepted = datetime(2026, 10, 12, 19, 0, tzinfo=UTC)          # 15:00 ET
    with get_session() as s:
        s.add(SecFilingRow(accession="a-same-day", cik=1, ticker="ZZZ", form="S-3", filed_at=accepted))
    p = ReferenceDataProvider()
    assert p.get("ZZZ", date(2026, 10, 12)).dilution_flag is False          # default: session start
    morning = datetime(2026, 10, 12, 13, 35, tzinfo=UTC)                    # 09:35 ET
    assert p.get("ZZZ", date(2026, 10, 12), as_of_ts=morning).dilution_flag is False
    after = datetime(2026, 10, 12, 19, 1, tzinfo=UTC)
    assert p.get("ZZZ", date(2026, 10, 12), as_of_ts=after).dilution_flag is True
    assert p.get("ZZZ", date(2026, 10, 13)).dilution_flag is True            # next session


def test_day_granular_snapshot_known_only_from_next_day():
    """R-1 regression: a companyfacts snapshot dated D is not usable during session D."""
    with get_session() as s:
        s.add(SharesSnapshotRow(ticker="YYY", period_end=_dt(2026, 9, 30),
                                filed_date=_dt(2026, 10, 12), source="edgar_dei", value=7_000_000))
    p = ReferenceDataProvider()
    late = datetime(2026, 10, 12, 23, 0, tzinfo=UTC)
    assert p.get("YYY", date(2026, 10, 12), as_of_ts=late).shares_outstanding is None
    assert p.get("YYY", date(2026, 10, 13)).shares_outstanding == 7_000_000


def test_naive_as_of_ts_rejected():
    with pytest.raises(ValueError):
        ReferenceDataProvider().get("ZZZ", date(2026, 10, 12), as_of_ts=datetime(2026, 10, 12, 10, 0))
