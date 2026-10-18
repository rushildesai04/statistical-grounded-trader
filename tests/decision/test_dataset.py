"""Decision-model labels, uniqueness weights, and dataset discovery gating
(docs/DECISION_MODEL.md §5, §8, §10)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pyarrow.parquet as pq
import pytest

from sgt.core.types import Bar, Segment
from sgt.decision.dataset import build_dataset
from sgt.decision.labels import LabelConfig, atr_bracket_label, default_grid, uniqueness_weights
from sgt.intraday.models import TrackedSymbolRow
from sgt.intraday.store import ParquetBarStore

_SD = date(2024, 3, 12)
_T0 = datetime(2024, 3, 12, 14, 0, tzinfo=timezone.utc)


def _bar(i, o, h, l, c, sd=_SD, v=100000.0) -> Bar:
    base = datetime(sd.year, sd.month, sd.day, 14, 0, tzinfo=timezone.utc)
    return Bar(symbol="AAAA", ts=base + timedelta(minutes=i), open=o, high=h, low=l, close=c,
               volume=v, session_date=sd, minute_of_day=600 + i, segment=Segment.RTH)


# ------------------------------------------------------------------ label math

def _cfg(k_tp=2.0, k_sl=1.0, h=10):
    return LabelConfig(k_tp=k_tp, k_sl=k_sl, h=h)


def test_label_take_profit_at_level():
    # signal idx 0; entry idx1 open=100, atr=1 -> tp=102, sl=99. idx2 high reaches 102.
    bars = [_bar(0, 100, 100.2, 99.8, 100), _bar(1, 100, 100.5, 99.5, 100.3),
            _bar(2, 100.3, 102.5, 100.2, 102.0)]
    out = atr_bracket_label(bars, 0, atr=1.0, half_spread=0.0, cfg=_cfg())
    assert out is not None and out.label == 1
    assert out.net_ret == pytest.approx(0.02)  # 102/100 - 1
    assert out.bars_held == 2 and out.entry_idx == 1 and out.exit_idx == 2


def test_label_gap_through_fills_at_open():
    bars = [_bar(0, 100, 100.2, 99.8, 100), _bar(1, 100, 100.4, 99.6, 100.2),
            _bar(2, 103.0, 103.5, 102.8, 103.2)]  # opens above tp 102
    out = atr_bracket_label(bars, 0, atr=1.0, half_spread=0.0, cfg=_cfg())
    assert out.label == 1 and out.net_ret == pytest.approx(0.03)  # fill at open 103


def test_label_same_bar_both_is_stop_first():
    bars = [_bar(0, 100, 100.2, 99.8, 100), _bar(1, 100, 103.0, 97.0, 100)]  # tp102 & sl99 both
    out = atr_bracket_label(bars, 0, atr=1.0, half_spread=0.0, cfg=_cfg())
    assert out.label == -1 and out.net_ret == pytest.approx(-0.01)  # sl 99/100 - 1


def test_label_truncated_returns_none():
    # Not enough bars for horizon, no barrier hit -> truncated (None).
    bars = [_bar(0, 100, 100.2, 99.8, 100), _bar(1, 100, 100.4, 99.6, 100.1)]
    assert atr_bracket_label(bars, 0, atr=1.0, half_spread=0.0, cfg=_cfg(h=10)) is None
    # Entry bar in a new session -> truncated.
    nxt = _bar(0, 100, 100.4, 99.6, 100.1, sd=date(2024, 3, 13))
    assert atr_bracket_label([_bar(0, 100, 100.2, 99.8, 100), nxt], 0, 1.0, 0.0, _cfg()) is None


def test_label_timeout_at_horizon_close():
    bars = [_bar(0, 100, 100.1, 99.9, 100)]
    bars += [_bar(i, 100.0, 100.3, 99.7, 100.0) for i in range(1, 6)]  # never hits tp102/sl99
    out = atr_bracket_label(bars, 0, atr=1.0, half_spread=0.0, cfg=_cfg(h=4))
    assert out is not None and out.label == 0 and out.bars_held == 4


def test_label_half_spread_reduces_net():
    bars = [_bar(0, 100, 100.2, 99.8, 100), _bar(1, 100, 102.5, 99.5, 102.0)]
    out = atr_bracket_label(bars, 0, atr=1.0, half_spread=0.01, cfg=_cfg())
    # entry 100*1.01, exit 102*0.99
    assert out.net_ret == pytest.approx(102 * 0.99 / (100 * 1.01) - 1.0)


# ------------------------------------------------------------------ uniqueness weights

def test_uniqueness_weights_overlap():
    w = uniqueness_weights([(1, 3), (2, 4)], n_bars=6)
    # bars: conc[1]=1,[2]=2,[3]=2,[4]=1. span(1,3): (1+0.5+0.5)/3; span(2,4): (0.5+0.5+1)/3
    assert w[0] == pytest.approx(2.0 / 3.0)
    assert w[1] == pytest.approx(2.0 / 3.0)


def test_uniqueness_weights_no_overlap():
    w = uniqueness_weights([(0, 1), (2, 3)], n_bars=4)
    assert w == [pytest.approx(1.0), pytest.approx(1.0)]


# ------------------------------------------------------------------ discovery gating

def _write_session(store: ParquetBarStore, sd: date) -> None:
    bars = [_bar(0, 20.0, 20.1, 19.9, 20.0, sd=sd)]
    c = 20.0
    for i in range(1, 25):  # rising -> hod_break fires repeatedly
        c += 0.1
        bars.append(_bar(i, c - 0.1, c + 0.2, c - 0.2, c, sd=sd))
    store.write_session("AAAA", sd, bars)


def test_discovery_gating_excludes_presence_before_first_seen(tmp_path):
    store = ParquetBarStore(tmp_path)
    sessions = [date(2024, 3, 11), date(2024, 3, 12), date(2024, 3, 13)]
    for sd in sessions:
        _write_session(store, sd)
    with_db()  # ensure tables
    _track("AAAA", first_seen="2024-03-12")

    summary = build_dataset(
        store_root=tmp_path, out_dir=tmp_path / "out", dataset_id="t",
        symbols=["AAAA"], reference_fn=lambda s, d: None)
    table = pq.read_table(summary.out_path)
    got = set(table.column("session_date").to_pylist())
    assert "2024-03-11" not in got  # before first_seen -> excluded
    assert {"2024-03-12", "2024-03-13"} <= got
    assert set(table.column("discovered_session").to_pylist()) == {"2024-03-12"}


def test_watchlist_symbol_is_never_gated(tmp_path):
    store = ParquetBarStore(tmp_path)
    for sd in [date(2024, 3, 11), date(2024, 3, 12)]:
        _write_session(store, sd)
    # No tracked row; AAAA only admitted via the watchlist.
    summary = build_dataset(
        store_root=tmp_path, out_dir=tmp_path / "out", dataset_id="t2",
        symbols=["AAAA"], watchlist=["AAAA"], reference_fn=lambda s, d: None)
    table = pq.read_table(summary.out_path)
    got = set(table.column("session_date").to_pylist())
    assert "2024-03-11" in got and "2024-03-12" in got


def test_dataset_has_full_label_grid_columns(tmp_path):
    store = ParquetBarStore(tmp_path)
    _write_session(store, date(2024, 3, 12))
    summary = build_dataset(
        store_root=tmp_path, out_dir=tmp_path / "out", dataset_id="t3",
        symbols=["AAAA"], watchlist=["AAAA"], reference_fn=lambda s, d: None)
    cols = set(pq.read_table(summary.out_path).column_names)
    for cfg in default_grid():
        assert f"y_net__{cfg.suffix}" in cols
        assert f"y_exit_bar__{cfg.suffix}" in cols
        assert f"uniq_w__{cfg.suffix}" in cols
    assert {"cost_rt_measured", "cost_rt_model", "cost_source", "q_float_degraded"} <= cols


# ------------------------------------------------------------------ helpers

def with_db() -> None:
    from sgt.db.session import init_db

    init_db()


def _track(symbol: str, *, first_seen: str) -> None:
    from sgt.db.session import get_session

    with get_session() as session:
        session.add(TrackedSymbolRow(
            symbol=symbol, first_seen_session=first_seen, last_seen_session=first_seen,
            sources=["test"], active_until_session="2099-01-01",
            created_at=datetime.now(timezone.utc)))
