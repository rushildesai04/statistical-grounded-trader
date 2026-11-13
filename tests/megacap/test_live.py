"""Forward-test ledger rules: append-only, void-after-15:55, fills at closes (prereg forward)."""
from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from sgt.megacap import live
from sgt.megacap.daily import DAILY_FEATURES

ET = ZoneInfo("America/New_York")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "ROOT", tmp_path)
    return tmp_path


def _features(n=400):
    rng = np.random.default_rng(0)
    sess = [d.date() for d in pd.bdate_range("2025-01-02", periods=n)]
    df = pd.DataFrame({f: rng.normal(size=n) for f in DAILY_FEATURES})
    df["session"], df["label"] = sess, rng.normal(0, 0.01, n)
    return df


def test_train_month_uses_only_earlier_rows_and_is_append_only(root):
    feats = {s: _features() for s in live.TICKERS}
    meta = live.train_month("2026-03", feats)
    assert all(m["last_session"] < "2026-03-01" for m in meta["tickers"].values())
    with pytest.raises(live.LiveError, match="already exist"):
        live.train_month("2026-03", feats)


def test_decide_append_only_and_late_void(root, monkeypatch):
    live.train_month("2026-03", {s: _features() for s in live.TICKERS})
    row = _features(1).iloc[0]
    monkeypatch.setattr(live, "today_row", lambda *a, **k: row)
    t = date(2026, 3, 18)
    p = live.decide(t, datetime(2026, 3, 18, 15, 50, tzinfo=ET), {}, {})
    dec = json.loads(p.read_text())
    assert set(dec["positions"]) == set(live.TICKERS)
    assert all(v["position"] in (-1, 0, 1) for v in dec["positions"].values())
    with pytest.raises(live.LiveError, match="append-only"):
        live.decide(t, datetime(2026, 3, 18, 15, 50, tzinfo=ET), {}, {})
    with pytest.raises(live.LiveError, match="void"):
        live.decide(date(2026, 3, 19), datetime(2026, 3, 19, 15, 56, tzinfo=ET), {}, {})


def test_mark_fills_at_closes_and_voids_late_files(root):
    (root / "decisions").mkdir()
    pos = {s: {"position": 0} for s in live.TICKERS} | {"AAPL": {"position": 1}, "MSFT": {"position": -1}}
    on_time = datetime(2026, 3, 18, 15, 50, 5, tzinfo=ET).isoformat()
    late = datetime(2026, 3, 19, 16, 10, tzinfo=ET).isoformat()
    (root / "decisions" / "2026-03-18.json").write_text(json.dumps({"date": "2026-03-18", "written_at": on_time, "positions": pos}))
    (root / "decisions" / "2026-03-19.json").write_text(json.dumps({"date": "2026-03-19", "written_at": late, "positions": pos}))
    idx = [date(2026, 3, 18), date(2026, 3, 19), date(2026, 3, 20)]
    daily = {s: pd.DataFrame({"close": [100.0, 102.0, 101.0]}, index=idx) for s in live.TICKERS}
    m = live.mark(daily).set_index(["date", "symbol"])
    assert m.loc[(date(2026, 3, 18), "AAPL"), "net"] == pytest.approx(0.02 - 0.0004)
    assert m.loc[(date(2026, 3, 18), "MSFT"), "net"] == pytest.approx(-0.02 - 0.0004)
    assert m.loc[(date(2026, 3, 18), "GOOGL"), "net"] == 0
    assert m.loc[(date(2026, 3, 19), "AAPL"), "void"] and m.loc[(date(2026, 3, 19), "AAPL"), "net"] == 0


def test_batch_mode_not_voided_and_hash_checked(root, monkeypatch):
    live.train_month("2026-03", {s: _features() for s in live.TICKERS})
    monkeypatch.setattr(live, "today_row", lambda *a, **k: _features(1).iloc[0])
    p = live.decide(date(2026, 3, 18), datetime(2026, 4, 2, 21, 0, tzinfo=ET), {}, {}, mode="batch")
    assert json.loads(p.read_text())["mode"] == "batch"
    idx = [date(2026, 3, 18), date(2026, 3, 19)]
    m = live.mark({s: pd.DataFrame({"close": [100.0, 101.0]}, index=idx) for s in live.TICKERS})
    assert not m.void.any()
    (root / "models" / "2026-03" / "AAPL.txt").write_text("tampered")
    with pytest.raises(live.LiveError, match="hash"):
        live.load_models("2026-03")


def test_last_complete_session_waits_for_store_completeness():
    # Regression 2026-10-30: at 17:30 ET the backfill still treats today as trading (post_close 20:00),
    # so catch-up must not try to decide today until post_close + 16 min.
    from megacap_forward import last_complete_session

    assert last_complete_session(datetime(2026, 10, 30, 17, 30, tzinfo=ET)) == date(2026, 10, 29)
    assert last_complete_session(datetime(2026, 10, 30, 20, 15, tzinfo=ET)) == date(2026, 10, 29)
    assert last_complete_session(datetime(2026, 10, 30, 20, 16, tzinfo=ET)) == date(2026, 10, 30)
    # Early close (day after Thanksgiving): post_close 17:00 ET.
    assert last_complete_session(datetime(2026, 11, 27, 17, 16, tzinfo=ET)) == date(2026, 11, 27)
    assert last_complete_session(datetime(2026, 11, 28, 9, 0, tzinfo=ET)) == date(2026, 11, 27)


def test_month_summaries_only_completed_months_cumulative():
    from megacap_forward import month_summaries

    port = pd.Series([0.01, -0.005, 0.002, 0.003],
                     index=[date(2026, 10, 30), date(2026, 11, 2), date(2026, 11, 30), date(2026, 12, 1)])
    out = month_summaries(port, date(2026, 12, 5))
    assert list(out) == ["2026-10", "2026-11"]                     # December still running
    assert out["2026-10"]["days"] == 1 and out["2026-10"]["cum_net"] == pytest.approx(0.01)
    assert out["2026-11"]["days"] == 3                               # cumulative since the start
    assert out["2026-11"]["cum_net"] == pytest.approx(1.01 * 0.995 * 1.002 - 1)
    assert month_summaries(port, date(2026, 10, 31)) == {}           # month end day itself: not over yet


# --- 2026-11-13: one unusable name must not cancel the whole 15:50 decision (RDDT, 186 IEX bars) ---
def _bars(day, minutes, px=100.0):
    ts = [pd.Timestamp(f"{day} 09:30", tz=ET).tz_convert("UTC") + pd.Timedelta(minutes=int(k)) for k in minutes]
    c = px * (1 + 0.0001 * np.arange(len(ts)))
    return pd.DataFrame({"ts": ts, "open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1000.0})


def _daily(day, n=300):
    idx = [d.date() for d in pd.bdate_range(end=day, periods=n)]
    c = 100 * np.cumprod(1 + np.random.default_rng(3).normal(0, 0.01, n))
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=idx)


def _live_inputs(day, sparse=None, stale=None):
    full = range(380)                                   # 09:30 .. 15:49
    intraday = {s: _bars(day, full) for s in (*live.TICKERS, "SPY", "QQQ")}
    if sparse:
        intraday[sparse[0]] = _bars(day, np.linspace(0, 379, sparse[1]).astype(int))
    if stale:
        intraday[stale] = _bars(day, range(300))        # last print 80 minutes before the market's last bar
    return intraday, {s: _daily(day) for s in (*live.TICKERS, "SPY", "QQQ")}


def test_a_thin_feed_name_is_still_decided_above_the_live_floor(root):
    day = date(2026, 3, 18)
    intraday, daily = _live_inputs(day, sparse=(live.TICKERS[0], 186))       # under the research floor of 195
    row = live.today_row(day, intraday, daily, live.TICKERS[0])
    assert row.session == day and np.isfinite(row.t_r_intraday)


@pytest.mark.parametrize("kind", ["too few bars", "stale"])
def test_an_unusable_name_is_flat_with_a_reason_and_the_set_still_decides(root, kind):
    live.train_month("2026-03", {s: _features() for s in live.TICKERS})
    day, bad = date(2026, 3, 18), live.TICKERS[0]
    intraday, daily = _live_inputs(day, sparse=(bad, live.LIVE_MIN_BARS - 1)) if kind == "too few bars" \
        else _live_inputs(day, stale=bad)
    dec = json.loads(live.decide(day, datetime(2026, 3, 18, 15, 50, tzinfo=ET), intraday, daily).read_text())
    assert dec["positions"][bad]["position"] == 0 and dec["positions"][bad]["skipped"]
    others = [v for s, v in dec["positions"].items() if s != bad]
    assert len(others) == len(live.TICKERS) - 1 and all("skipped" not in v and v["pred"] is not None for v in others)


def test_no_market_context_decides_nothing(root):
    live.train_month("2026-03", {s: _features() for s in live.TICKERS})
    day = date(2026, 3, 18)
    intraday, daily = _live_inputs(day)
    intraday["SPY"] = intraday["SPY"].iloc[:0]
    with pytest.raises(live.LiveError, match="no name could be decided"):
        live.decide(day, datetime(2026, 3, 18, 15, 50, tzinfo=ET), intraday, daily)
    assert not (root / "decisions" / f"{day}.json").exists()


def test_research_floor_is_unchanged():
    from sgt.megacap.features import to_grid
    day = date(2026, 3, 18)
    thin = _bars(day, np.linspace(0, 379, 186).astype(int))
    assert day not in to_grid(thin) and day in to_grid(thin, live.LIVE_MIN_BARS)
