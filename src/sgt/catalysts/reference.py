"""Point-in-time reference data (shares outstanding, free float, dilution flag).

Every value is reconstructed as it was *knowable* on ``as_of``: only rows whose
filing/effective date is ``<= as_of`` are used, so backtests and shadow replay can
never see data that was filed after the decision time. Free float is a manual
override when one is effective, otherwise shares-outstanding is returned as an
explicit proxy (``float_source="edgar_dei_outstanding_proxy"``) — not a true float.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sgt.catalysts.filings import DILUTION_FORMS
from sgt.catalysts.models import FloatOverrideRow, SecFilingRow, SharesSnapshotRow
from sgt.core.interfaces import ReferenceData
from sgt.db.session import get_session

_UTC = timezone.utc
FLOAT_PROXY_SOURCE = "edgar_dei_outstanding_proxy"
DEFAULT_LOOKBACK_DAYS = 90


_ET = ZoneInfo("America/New_York")


def _session_start_cutoff(d: date) -> datetime:
    """Default knowledge cutoff for a session date: 04:00 ET (pre-market open), UTC.

    2026-10-13, review R-1: the previous end-of-day cutoff let an intraday decision
    see a filing accepted later the same day (look-ahead). Callers that need
    intraday freshness pass an explicit ``as_of_ts``.
    """
    return datetime.combine(d, time(4, 0), tzinfo=_ET).astimezone(_UTC)


class ReferenceDataProvider:
    def __init__(self, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> None:
        self.lookback_days = lookback_days

    def get(self, symbol: str, as_of: date, *, as_of_ts: datetime | None = None) -> ReferenceData:
        """Point-in-time reference facts known at ``as_of_ts`` (default: 04:00 ET on ``as_of``).

        ``filed_at`` (EDGAR acceptance time) is compared exactly. ``filed_date`` carries
        only day granularity, so a snapshot counts as known from the START of the next
        day — never earlier than it could have been read.
        """
        symbol = symbol.upper()
        if as_of_ts is not None and (as_of_ts.tzinfo is None or as_of_ts.utcoffset() is None):
            raise ValueError("as_of_ts must be tz-aware")
        cutoff = (as_of_ts.astimezone(_UTC) if as_of_ts is not None else _session_start_cutoff(as_of))
        day_floor = datetime.combine(cutoff.astimezone(_ET).date(), time.min, tzinfo=_UTC)
        dilution_start = datetime.combine(
            as_of - timedelta(days=self.lookback_days), time.min, tzinfo=_UTC
        )

        with get_session() as session:
            snap = (
                session.query(SharesSnapshotRow)
                .filter(
                    SharesSnapshotRow.ticker == symbol,
                    SharesSnapshotRow.filed_date < day_floor,
                )
                .order_by(
                    SharesSnapshotRow.filed_date.desc(),
                    SharesSnapshotRow.period_end.desc(),
                )
                .first()
            )
            override = (
                session.query(FloatOverrideRow)
                .filter(
                    FloatOverrideRow.ticker == symbol,
                    FloatOverrideRow.effective_date <= cutoff,
                )
                .order_by(FloatOverrideRow.effective_date.desc())
                .first()
            )
            dilution = (
                session.query(SecFilingRow)
                .filter(
                    SecFilingRow.ticker == symbol,
                    SecFilingRow.form.in_(DILUTION_FORMS),
                    SecFilingRow.filed_at >= dilution_start,
                    SecFilingRow.filed_at <= cutoff,
                )
                .first()
            )

            shares_outstanding = snap.value if snap is not None else None
            if override is not None:
                free_float: float | None = override.free_float
                float_source = override.source or "manual"
            elif shares_outstanding is not None:
                free_float = shares_outstanding
                float_source = FLOAT_PROXY_SOURCE
            else:
                free_float = None
                float_source = ""

            return ReferenceData(
                symbol=symbol,
                as_of=as_of,
                shares_outstanding=shares_outstanding,
                free_float=free_float,
                float_source=float_source,
                dilution_flag=dilution is not None,
                short_interest_pct_float=None,
            )
