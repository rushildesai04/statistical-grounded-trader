"""ParquetBarStore: round-trip, immutability, atomicity, validation, discovery."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from sgt.core.types import Segment
from sgt.intraday.calendar import default_calendar
from sgt.intraday.store import ParquetBarStore
from sgt.intraday.synthetic import make_session_bars

UTC = timezone.utc
D = date(2025, 3, 14)


@pytest.fixture
def store(tmp_path) -> ParquetBarStore:
    return ParquetBarStore(tmp_path)


@pytest.fixture
def bars():
    return make_session_bars(
        "WFF", D, segments=(Segment.PRE, Segment.RTH, Segment.POST), seed=3
    )


def test_round_trip_identical_bars(store, bars):
    store.write_session("WFF", D, bars)
    out = store.read("WFF", D, D)
    assert out == bars  # frozen dataclass equality, field-for-field


def test_round_trip_preserves_optional_fields(store):
    cal = default_calendar()
    ts = datetime(2025, 3, 14, 15, 0, tzinfo=UTC)
    b = cal.annotate("ZZZ", ts, 2.0, 2.2, 1.9, 2.1, 500.0, vwap=2.05, trade_count=42)
    b_none = cal.annotate("ZZZ", datetime(2025, 3, 14, 15, 1, tzinfo=UTC), 2.1, 2.2, 2.0, 2.15, 600.0)
    store.write_session("ZZZ", D, [b, b_none])
    out = store.read("ZZZ", D, D)
    assert out[0].vwap == 2.05 and out[0].trade_count == 42
    assert out[1].vwap is None and out[1].trade_count is None
    assert out == [b, b_none]


def test_file_layout(store, bars, tmp_path):
    path = store.write_session("WFF", D, bars)
    assert path == tmp_path / "bars_1m" / "date=2025-03-14" / "symbol=WFF.parquet"
    assert path.exists()


def test_immutability_refuses_overwrite(store, bars):
    store.write_session("WFF", D, bars)
    with pytest.raises(FileExistsError):
        store.write_session("WFF", D, bars)
    store.write_session("WFF", D, bars, overwrite=True)  # explicit override allowed


def test_atomic_write_leaves_no_tmp_file(store, bars):
    store.write_session("WFF", D, bars)
    date_dir = store.root / "date=2025-03-14"
    assert not list(date_dir.glob("*.tmp*"))


def test_rejects_wrong_symbol(store, bars):
    with pytest.raises(ValueError, match="symbol"):
        store.write_session("XXX", D, bars)


def test_rejects_wrong_session_date(store, bars):
    with pytest.raises(ValueError, match="session_date"):
        store.write_session("WFF", date(2025, 3, 13), bars)


def test_rejects_non_increasing_ts(store):
    cal = default_calendar()
    a = cal.annotate("WFF", datetime(2025, 3, 14, 15, 0, tzinfo=UTC), 5, 5.1, 4.9, 5, 10)
    dup = cal.annotate("WFF", datetime(2025, 3, 14, 15, 0, tzinfo=UTC), 5, 5.1, 4.9, 5, 10)
    with pytest.raises(ValueError, match="strictly increasing"):
        store.write_session("WFF", D, [a, dup])


def test_rejects_empty(store):
    with pytest.raises(ValueError, match="at least one"):
        store.write_session("WFF", D, [])


def test_read_inclusive_range_and_ordering(store):
    d1, d2 = date(2025, 3, 13), date(2025, 3, 14)
    b1 = make_session_bars("WFF", d1, seed=1)
    b2 = make_session_bars("WFF", d2, seed=2)
    store.write_session("WFF", d1, b1)
    store.write_session("WFF", d2, b2)
    out = store.read("WFF", d1, d2)
    assert out == b1 + b2
    assert all(out[i].ts < out[i + 1].ts for i in range(len(out) - 1))
    # end-exclusive-of-range check: only d1
    assert store.read("WFF", d1, d1) == b1


def test_discovery(store, bars):
    store.write_session("WFF", D, bars)
    store.write_session("AAA", D, make_session_bars("AAA", D, seed=4))
    assert store.sessions("WFF") == [D]
    assert store.symbols(D) == ["AAA", "WFF"]
    assert store.symbols() == ["AAA", "WFF"]
    assert store.symbols(date(2099, 1, 1)) == []


def test_read_frame_columns(store, bars):
    store.write_session("WFF", D, bars)
    df = store.read_frame("WFF", D, D)
    assert list(df.columns) == [
        "ts", "symbol", "open", "high", "low", "close", "volume",
        "vwap", "trade_count", "session_date", "minute_of_day", "segment",
    ]
    assert len(df) == len(bars)


def test_read_missing_symbol_empty(store):
    assert store.read("NOPE", D, D) == []
    assert store.read_frame("NOPE", D, D).empty
