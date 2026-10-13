"""SEC EDGAR filings + shares-snapshot collection (mocked httpx)."""
from __future__ import annotations

import httpx
import pytest
import respx

from sgt.catalysts.filings import FilingsCollector
from sgt.catalysts.models import SecFilingRow, SharesSnapshotRow
from sgt.db.session import get_session

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUB_URL = "https://data.sec.gov/submissions/CIK0000320193.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"

_TICKERS = {"0": {"cik_str": 320193, "ticker": "WFF", "title": "Worldwide Flagship Fund"}}
_SUBMISSIONS = {
    "cik": "320193",
    "filings": {
        "recent": {
            "accessionNumber": ["0001-24-000001", "0001-24-000002", "0001-24-000003"],
            "form": ["S-3", "10-K", "424B5"],
            "filingDate": ["2026-09-01", "2026-08-01", "2026-09-15"],
            "acceptanceDateTime": [
                "2026-09-01T16:30:00.000Z", "2026-08-01T17:00:00.000Z", "2026-09-15T09:00:00.000Z",
            ],
        }
    },
}
_FACTS = {
    "facts": {
        "dei": {
            "EntityCommonStockSharesOutstanding": {
                "units": {
                    "shares": [
                        {"val": 10_000_000, "end": "2026-06-30", "filed": "2026-07-15", "form": "10-Q"},
                        {"val": 12_500_000, "end": "2026-09-30", "filed": "2026-10-20", "form": "10-Q"},
                    ]
                }
            }
        }
    }
}


@pytest.fixture
def mocked_edgar():
    with respx.mock:
        respx.get(TICKERS_URL).mock(return_value=httpx.Response(200, json=_TICKERS))
        respx.get(SUB_URL).mock(return_value=httpx.Response(200, json=_SUBMISSIONS))
        respx.get(FACTS_URL).mock(return_value=httpx.Response(200, json=_FACTS))
        yield


def _collector():
    return FilingsCollector(client=httpx.Client(), sleep=lambda *_: None)


def test_collect_counts(mocked_edgar):
    counts = _collector().collect("WFF")
    assert counts == {"filings": 3, "shares_snapshots": 2}


def test_filings_persisted(mocked_edgar):
    _collector().collect("WFF")
    with get_session() as s:
        forms = {r.accession: r.form for r in s.query(SecFilingRow).all()}
    assert forms["0001-24-000001"] == "S-3"
    assert forms["0001-24-000003"] == "424B5"


def test_shares_snapshots_persisted(mocked_edgar):
    _collector().collect("WFF")
    with get_session() as s:
        snaps = {r.period_end.date().isoformat(): r.value for r in s.query(SharesSnapshotRow).all()}
    assert snaps["2026-06-30"] == 10_000_000
    assert snaps["2026-09-30"] == 12_500_000


def test_collect_idempotent(mocked_edgar):
    c = _collector()
    c.collect("WFF")
    c.collect("WFF")
    with get_session() as s:
        assert s.query(SecFilingRow).count() == 3
        assert s.query(SharesSnapshotRow).count() == 2


def test_cik_lookup_and_user_agent(mocked_edgar):
    c = _collector()
    assert c.cik_for("WFF") == 320193
    assert c.cik_for("NOPE") is None


def test_auth_error_raises():
    from sgt.catalysts.filings import EdgarAuthError

    with respx.mock:
        respx.get(TICKERS_URL).mock(return_value=httpx.Response(403, text="forbidden"))
        with pytest.raises(EdgarAuthError):
            _collector().collect("WFF")
