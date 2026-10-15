"""Daily accumulation job: discovery validation, tracking window (weekend-aware),
capture (idempotent, immutable, completeness/missing-session gating), SEC + vendor
float collection, and dry-run. No network: screener/source/filings/float_source/
sleep/clock are all fakes.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from sgt.catalysts.models import FloatOverrideRow, SharesSnapshotRow
from sgt.db.session import get_session
from sgt.intraday.accumulate import (
    VENDOR_FLOAT_SOURCE,
    Discovered,
    discover,
    run_daily,
)
from sgt.intraday.calendar import default_calendar
from sgt.intraday.models import TrackedSymbolRow
from sgt.intraday.store import ParquetBarStore
from sgt.intraday.synthetic import make_session_bars

UTC = timezone.utc
CAL = default_calendar()

# 2026-10-13 23:00 UTC (Tue). The 10-13 session is NOT yet complete (post-close is
# 00:00 UTC 10-14); 10-07/08/09/12 are complete sessions within a 5-session lookback.
NOW = datetime(2026, 10, 13, 23, 0, tzinfo=UTC)
CUR = date(2026, 10, 13)
COMPLETE = [date(2026, 10, 7), date(2026, 10, 8), date(2026, 10, 9), date(2026, 10, 12)]


# --------------------------------------------------------------------------- fakes


def _q(symbol, price, chg=5.0):
    return {"symbol": symbol, "regularMarketPrice": price, "regularMarketChangePercent": chg}


def screener_from(mapping, *, fail=()):
    def _screen(query, count=100):
        if query in fail:
            raise RuntimeError(f"boom {query}")
        return {"quotes": list(mapping.get(query, []))[:count]}

    return _screen


class FakeSource:
    """Returns pre-canned bars keyed by (symbol, session_date); records fetch calls."""

    name = "fake"

    def __init__(self, data=None):
        self.data = data or {}
        self.calls = []

    def fetch(self, symbol, start, end):
        sdate = CAL.session_of(start + (end - start) / 2) or start.date()
        self.calls.append((symbol, sdate))
        return list(self.data.get((symbol, sdate), []))


class FakeFilings:
    """Mirrors FilingsCollector: unknown ticker -> EdgarError; auth -> EdgarAuthError."""

    def __init__(self, no_cik=(), no_shares=(), auth_fail=False):
        self.calls = []
        self.no_cik = set(no_cik)
        self.no_shares = set(no_shares)
        self.auth_fail = auth_fail

    def collect(self, ticker):
        from sgt.catalysts.filings import EdgarAuthError, EdgarError

        self.calls.append(ticker)
        if self.auth_fail:
            raise EdgarAuthError("HTTP 403 (User-Agent rejected)")
        if ticker in self.no_cik:
            raise EdgarError(f"no CIK for ticker {ticker!r}")
        return {"filings": 1, "shares_snapshots": 0 if ticker in self.no_shares else 1}


class FakeFloat:
    def __init__(self, values):
        self.values = values  # symbol -> (float_shares, shares_out)
        self.calls = []

    def __call__(self, symbol):
        self.calls.append(symbol)
        return self.values.get(symbol, (None, None))


class Sleeps:
    def __init__(self):
        self.calls = []

    def __call__(self, secs):
        self.calls.append(secs)


@pytest.fixture
def store(tmp_path):
    return ParquetBarStore(tmp_path)


def _bars(symbol, sdate):
    return make_session_bars(symbol, sdate, seed=abs(hash((symbol, sdate))) % 1000)


# --------------------------------------------------------------------------- discovery


def test_discover_rejects_bad_symbols_and_prices():
    quotes = [
        _q("WFF", 3.0),          # ok
        _q("BRK.A", 4.0),        # ok, class A
        _q("bf.b", 4.0),         # ok, lowercased -> BF.B
        _q("ABCDEF", 4.0),       # reject: 6 letters
        _q("BRK.C", 4.0),        # reject: class C
        _q("A1", 4.0),           # reject: non-alpha base
        _q("123", 4.0),          # reject: numeric
        _q("LOW", 0.1),          # reject: below price_min
        _q("HIGH", 500.0),       # reject: above price_max
        {"symbol": "NOPX", "regularMarketPrice": "notnum"},  # reject: non-numeric price
        {"regularMarketPrice": 5.0},                          # reject: no symbol
    ]
    out = discover(screener_from({"day_gainers": quotes}), queries=("day_gainers",))
    assert sorted(d.symbol for d in out) == ["BF.B", "BRK.A", "WFF"]


def test_discover_bounds_count():
    quotes = [_q("AAA", 2.0), _q("WFF", 3.0), _q("YMAT", 4.0), _q("SPCX", 5.0)]
    out = discover(screener_from({"q": quotes}), queries=("q",), count=0)  # clamped to >=1
    assert len(out) == 1  # only the first quote survives the clamp


def test_discover_one_query_failure_does_not_abort():
    from sgt.intraday.accumulate import AccumulationReport

    rep = AccumulationReport(now=NOW, current_session=CUR)
    mapping = {"a": [_q("WFF", 3.0)], "b": [_q("YMAT", 4.0)]}
    out = discover(
        screener_from(mapping, fail=("a",)), queries=("a", "b"), report=rep
    )
    assert sorted(d.symbol for d in out) == ["YMAT"]
    assert rep.errors.get("discovery") == 1


def test_discover_dedupes_within_query():
    quotes = [_q("WFF", 3.0), _q("WFF", 3.1)]
    out = discover(screener_from({"q": quotes}), queries=("q",))
    assert [d.symbol for d in out] == ["WFF"]


# --------------------------------------------------------------------------- tracking window


def _run(now, screener, source, store, **kw):
    return run_daily(
        now,
        screener=screener,
        source=source,
        store=store,
        calendar=CAL,
        sleep=Sleeps(),
        queries=("g",),
        collect_sec_float=False,
        collect_vendor_float=False,
        **kw,
    )


def test_tracking_window_extends_on_rediscovery(store):
    src = FakeSource()
    scr = screener_from({"g": [_q("WFF", 3.0)]})
    # discover on Fri 2026-10-02, keep_sessions=2 -> active_until = 10-06
    _run(datetime(2026, 10, 2, 23, tzinfo=UTC), scr, src, store, keep_sessions=2)
    with get_session() as s:
        row = s.query(TrackedSymbolRow).filter_by(symbol="WFF").one()
        assert row.first_seen_session == "2026-10-02"
        assert row.active_until_session == "2026-10-06"
    # re-discover on Mon 2026-10-05 -> window extends to 10-07
    _run(datetime(2026, 10, 5, 23, tzinfo=UTC), scr, src, store, keep_sessions=2)
    with get_session() as s:
        row = s.query(TrackedSymbolRow).filter_by(symbol="WFF").one()
        assert row.first_seen_session == "2026-10-02"  # unchanged
        assert row.last_seen_session == "2026-10-05"
        assert row.active_until_session == "2026-10-07"


def test_tracking_expires_after_keep_sessions(store):
    src = FakeSource()
    _run(datetime(2026, 10, 2, 23, tzinfo=UTC),
         screener_from({"g": [_q("WFF", 3.0)]}), src, store, keep_sessions=2)
    # later run (Wed 10-07) with NO re-discovery: active_until 10-06 < 10-07 -> inactive
    rep = _run(datetime(2026, 10, 7, 23, tzinfo=UTC),
               screener_from({"g": []}), FakeSource(), store, keep_sessions=2)
    assert rep.active == 0  # WFF expired, nothing else tracked
    assert ("WFF", date(2026, 10, 6)) not in src.calls


def test_static_watchlist_always_active_and_captured(store):
    data = {("ZZZ", d): _bars("ZZZ", d) for d in COMPLETE}
    src = FakeSource(data)
    rep = _run(NOW, screener_from({"g": []}), src, store, watchlist=["ZZZ"])
    assert rep.active == 1
    assert rep.sessions_written == len(COMPLETE)  # all complete sessions captured
    assert set(store.sessions("ZZZ")) == set(COMPLETE)


# --------------------------------------------------------------------------- capture


def test_capture_writes_missing_skips_existing_and_incomplete(store):
    data = {("WFF", d): _bars("WFF", d) for d in COMPLETE}
    # Pre-write one session so it is skipped.
    store.write_session("WFF", COMPLETE[0], data[("WFF", COMPLETE[0])])
    src = FakeSource(data)
    rep = _run(NOW, screener_from({"g": [_q("WFF", 3.0)]}), src, store, lookback_sessions=5)
    # 4 complete sessions, 1 pre-existing -> 3 written. The 10-13 session is incomplete.
    assert rep.sessions_written == 3
    assert (CUR not in store.sessions("WFF"))  # incomplete current session never written
    assert ("WFF", CUR) not in src.calls       # never even fetched
    assert set(store.sessions("WFF")) == set(COMPLETE)


def test_capture_is_idempotent(store):
    data = {("WFF", d): _bars("WFF", d) for d in COMPLETE}
    src = FakeSource(data)
    scr = screener_from({"g": [_q("WFF", 3.0)]})
    first = _run(NOW, scr, src, store)
    assert first.sessions_written == len(COMPLETE)
    src2 = FakeSource(data)
    second = _run(NOW, scr, src2, store)
    assert second.sessions_written == 0
    assert second.bars_written == 0
    assert src2.calls == []  # existing sessions are not re-fetched


def test_capture_empty_fetch_recorded_not_written(store):
    # Source returns nothing for WFF: recorded as no-data, no file created.
    src = FakeSource({})
    rep = _run(NOW, screener_from({"g": [_q("WFF", 3.0)]}), src, store)
    assert rep.no_data == len(COMPLETE)
    assert rep.sessions_written == 0
    assert store.sessions("WFF") == []


def test_capture_never_overwrites(store):
    """A file already on disk is never clobbered even if fetch returns fresh bars."""
    sdate = COMPLETE[1]
    original = _bars("WFF", sdate)
    store.write_session("WFF", sdate, original)
    before = store.read("WFF", sdate, sdate)
    # Source would return a DIFFERENT bar set for that session; capture must skip it.
    other = make_session_bars("WFF", sdate, seed=999)
    src = FakeSource({("WFF", sdate): other})
    _run(NOW, screener_from({"g": [_q("WFF", 3.0)]}), src, store)
    assert store.read("WFF", sdate, sdate) == before


def test_max_symbols_caps_capture(store):
    syms = ["WFF", "YMAT", "SPCX", "ZZZ"]
    data = {(s, d): _bars(s, d) for s in syms for d in COMPLETE}
    src = FakeSource(data)
    scr = screener_from({"g": [_q(s, 3.0) for s in syms]})
    rep = _run(NOW, scr, src, store, max_symbols=2)
    assert rep.active == 2
    captured = {sym for sym, _ in src.calls}
    assert len(captured) == 2


# --------------------------------------------------------------------------- float


def _run_float(now, store, *, filings=None, float_source=None, **kw):
    return run_daily(
        now,
        screener=screener_from({"g": [_q("WFF", 3.0), _q("YMAT", 4.0), _q("SPCX", 5.0)]}),
        source=FakeSource(),
        store=store,
        calendar=CAL,
        sleep=Sleeps(),
        queries=("g",),
        filings=filings,
        float_source=float_source,
        **kw,
    )


def test_sec_float_only_for_symbols_without_snapshots(store):
    # WFF already has a snapshot -> skipped. SPCX has no CIK -> counted no_sec_data.
    with get_session() as s:
        s.add(SharesSnapshotRow(ticker="WFF", period_end=datetime(2026, 6, 30, tzinfo=UTC),
                                filed_date=datetime(2026, 7, 15, tzinfo=UTC),
                                source="edgar_dei", value=5_040_000))
    filings = FakeFilings(no_cik=("SPCX",))
    rep = _run_float(NOW, store, filings=filings, collect_vendor_float=False)
    assert "WFF" not in filings.calls
    assert sorted(filings.calls) == ["SPCX", "YMAT"]
    assert rep.float_collected == 1   # YMAT
    assert rep.no_sec_data == 1       # SPCX


def test_vendor_float_persisted_and_precedence_source(store):
    fs = FakeFloat({"WFF": (38_500_000.0, 41_900_000.0), "YMAT": (None, 12_000_000.0)})
    rep = _run_float(NOW, store, float_source=fs, collect_sec_float=False,
                     watchlist=[], max_symbols=200)
    assert rep.vendor_float_collected == 2  # WFF (float), YMAT (shares_out proxy)
    with get_session() as s:
        wff = s.query(FloatOverrideRow).filter_by(ticker="WFF").one()
        assert wff.free_float == 38_500_000.0
        assert wff.source == VENDOR_FLOAT_SOURCE
        # Stamped at OBSERVATION time (no same-day look-ahead); SQLite round-trips tz-naive.
        assert wff.effective_date.replace(tzinfo=UTC) == NOW.astimezone(UTC)
        ymat = s.query(FloatOverrideRow).filter_by(ticker="YMAT").one()
        assert ymat.free_float == 12_000_000.0  # falls back to shares_outstanding


def test_vendor_float_idempotent_and_not_backdated(store):
    fs = FakeFloat({"WFF": (38_500_000.0, 41_900_000.0)})
    _run_float(NOW, store, float_source=fs, collect_sec_float=False)
    _run_float(NOW, store, float_source=FakeFloat({"WFF": (38_500_000.0, 41_900_000.0)}),
               collect_sec_float=False)
    with get_session() as s:
        rows = s.query(FloatOverrideRow).filter_by(ticker="WFF").all()
        assert len(rows) == 1  # one row per (symbol, session) across both runs
        eff = rows[0].effective_date.replace(tzinfo=UTC)
        assert eff == NOW.astimezone(UTC)  # observation time, not session midnight
        assert eff <= NOW  # never stamped in the future / backdated


def test_vendor_float_invalid_values_rejected(store):
    fs = FakeFloat({
        "WFF": (0.0, 0.0),          # non-positive -> invalid
        "YMAT": (float("nan"), None),  # NaN -> invalid
        "SPCX": (-5.0, None),       # negative -> invalid
    })
    rep = _run_float(NOW, store, float_source=fs, collect_sec_float=False)
    assert rep.vendor_float_collected == 0
    assert rep.vendor_float_missing == 3
    with get_session() as s:
        assert s.query(FloatOverrideRow).count() == 0


# --------------------------------------------------------------------------- dry-run


def test_dry_run_writes_nothing(store):
    data = {("WFF", d): _bars("WFF", d) for d in COMPLETE}
    src = FakeSource(data)
    fs = FakeFloat({"WFF": (38_500_000.0, 41_900_000.0)})
    rep = run_daily(
        NOW,
        screener=screener_from({"g": [_q("WFF", 3.0)]}),
        source=src,
        store=store,
        calendar=CAL,
        sleep=Sleeps(),
        queries=("g",),
        filings=FakeFilings(),
        float_source=fs,
        watchlist=["ZZZ"],
        dry_run=True,
    )
    assert rep.active >= 1
    assert rep.planned_sessions > 0
    assert rep.sessions_written == 0
    assert src.calls == []  # planning never fetches
    assert store.sessions("WFF") == []
    with get_session() as s:
        assert s.query(TrackedSymbolRow).count() == 0
        assert s.query(FloatOverrideRow).count() == 0


def test_report_to_markdown_smoke(store):
    rep = _run(NOW, screener_from({"g": [_q("WFF", 3.0)]}), FakeSource(), store)
    md = rep.to_markdown()
    assert "Daily accumulation" in md
    assert "Active symbols" in md


def test_vendor_float_invisible_to_decisions_earlier_the_same_day(store):
    """Look-ahead guard: a float observed after the close must not be visible to a
    decision earlier that session, but must be visible the next session."""
    from datetime import date, timedelta

    from sgt.catalysts.reference import ReferenceDataProvider

    _run_float(NOW, store, float_source=FakeFloat({"WFF": (38_500_000.0, 41_900_000.0)}),
               collect_sec_float=False)
    p = ReferenceDataProvider()
    earlier = NOW - timedelta(hours=6)
    assert p.get("WFF", NOW.date(), as_of_ts=earlier).free_float != 38_500_000.0
    assert p.get("WFF", NOW.date() + timedelta(days=1)).free_float == 38_500_000.0


def test_sec_auth_failure_stops_stage_and_is_an_error(store):
    """An EDGAR UA/auth rejection must surface as an error, not as 'no SEC data' x N."""
    filings = FakeFilings(auth_fail=True)
    rep = _run_float(NOW, store, filings=filings, collect_vendor_float=False)
    assert len(filings.calls) == 1            # stage stopped after the first rejection
    assert rep.errors.get("sec_float") == 1
    assert rep.no_sec_data == 0 and rep.float_collected == 0


def test_in_edgar_without_share_facts_counts_as_no_sec_data(store):
    filings = FakeFilings(no_shares=("YMAT",))
    rep = _run_float(NOW, store, filings=filings, collect_vendor_float=False)
    assert rep.float_collected == len(filings.calls) - 1
    assert rep.no_sec_data == 1
